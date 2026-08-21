#!/usr/bin/env python3
# Copyright (c) 2025 Bojan Novković <bnovkov@FreeBSD.org>
#
# SPDX-License-Identifier: BSD-2-Clause

import os
import time
import json
import gzip
import atexit
import datetime
import tempfile
import subprocess
import mmap
import re

from contextlib import contextmanager
from pathlib import Path

from typing import Dict

SRC_REPO_PHID = "PHID-REPO-liw4oec7metux67nyavs"
IMP_PHID = "PHID-USER-q5lmute3rwskeizvu5gf"
GIT_DIR = "./freebsd-src"
CSV_DIR = "/home/bnovkov/public_html/stale_reviews"
PHID_CACHE = "phids.json.gz"

responses = []
phid_cache = {}
stale_date_thresh = datetime.date.today() - datetime.timedelta(weeks=3)
git_logfile = tempfile.NamedTemporaryFile(delete=False)
review_query = {
    "after": None,
    "constraints": {"statuses": ["accepted", "needs-review"]},
    "attachments": {
        "subscribers": True,
        "reviewers": True,
        "projects": True,
    },
}
db = {
    "external_author": {
        "author_string": "external",
        "needs_close": [],
        "not_landed": [],
        "stale": [],
        "stale_no_reviewers": [],
    },
    "fbsd_author": {
        "author_string": "@FreeBSD.org",
        "needs_close": [],
        "not_landed": [],
        "stale": [],
        "stale_no_reviewers": [],
    },
}


def call_conduit(api: str, query: Dict) -> Dict:
    result = subprocess.run(
        [
            "arc",
            "--config",
            "phabricator.uri=https://reviews.freebsd.org",
            "call-conduit",
            "--",
            api,
        ],
        stdout=subprocess.PIPE,
        input=json.dumps(query).encode(),
    )
    return json.loads(result.stdout.decode())


def fetch_revisions() -> None:
    response = call_conduit("differential.revision.search", review_query)
    responses.append(response)

    return response["response"]["cursor"]["after"]


def lookup_phid(phid: str) -> Dict:
    if phid in phid_cache:
        return phid_cache[phid]

    response = call_conduit("phid.lookup", {"names": [phid]})
    phid_cache[phid] = response["response"][phid]

    return response["response"][phid]


def dump_html(file_prefix: str, review_dict: Dict) -> None:
    cols = ["ID", "Title", "LastModified", "URL"]

    for key, reviews in review_dict.items():
        if key == "author_string":
            continue
        filename = "{}_{}.html".format(file_prefix, key)
        with open(filename, "w") as htmlfile:
            htmlfile.write("<table>\n")
            htmlfile.write("<tr>")
            htmlfile.write("".join(f"<th>{col}</th>" for col in cols))
            htmlfile.write("</tr>\n")
            for review_data in reviews:
                uri = review_data["fields"]["uri"]
                id = review_data["id"]
                row = [
                    id,
                    review_data["fields"]["title"],
                    datetime.date.fromtimestamp(
                        review_data["fields"]["dateModified"]
                    ).strftime("%Y_%m_%d"),
                    f"<a href={uri}>D{id}</a>",
                ]
                htmlfile.write("<tr>")
                htmlfile.write("".join(f"<td>{cell}</td>" for cell in row))
                htmlfile.write("</tr>\n")
            htmlfile.write("</table>")


def save_phid_cache() -> None:
    with gzip.open(PHID_CACHE, "wt", encoding="utf-8") as phid_file:
        json.dump(phid_cache, phid_file)


@contextmanager
def chdir(dir: Path) -> None:
    curdir = os.getcwd()
    os.chdir(dir)
    try:
        yield
    finally:
        os.chdir(curdir)


try:
    with gzip.open(PHID_CACHE, "rt", encoding="utf-8") as phid_file:
        phid_cache = json.load(phid_file)
except FileNotFoundError:
    pass
except Exception as e:
    print("Failed to load PHID cache: ", e)
    exit(-1)

# Output contents of 'git --log' into a file
subprocess.run(
    [
        "git",
        "--no-pager",
        "-C",
        GIT_DIR,
        "log",
    ],
    stdout=git_logfile,
    text=True,
)

# Fetch all accepted and open revisions
while True:
    next_revid = fetch_revisions()
    if not next_revid:
        break
    review_query["after"] = next_revid
    time.sleep(1)

atexit.register(save_phid_cache)
atexit.register(lambda: os.remove(git_logfile.name))

# Filter and categorize revisions
with open(git_logfile.name, mode="r") as file:
    with mmap.mmap(file.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
        for response in responses:
            for review_data in response["response"]["data"]:
                fields = review_data["fields"]
                status = fields["status"]
                last_modified = datetime.date.fromtimestamp(fields["dateModified"])
                author_data = lookup_phid(fields["authorPHID"])
                storage = (
                    db["external_author"]
                    if "_" in author_data["name"]
                    else db["fbsd_author"]
                )
                subscribers = list(
                    filter(
                        lambda x: x != IMP_PHID,
                        review_data["attachments"]["subscribers"]["subscriberPHIDs"],
                    )
                )
                reviewers = review_data["attachments"]["reviewers"]["reviewers"]

                # Skip non-src reviews
                if fields["repositoryPHID"] != SRC_REPO_PHID:
                    continue
                if last_modified > stale_date_thresh:
                    continue

                # Categorize the stale review
                if status["name"] == "Accepted":
                    pat = re.compile(
                        "https://reviews.freebsd.org/D{}".format(
                            review_data["id"]
                        ).encode()
                    )
                    if pat.search(mm) or fields["title"].encode() in mm:
                        storage["needs_close"].append(review_data)
                    else:
                        storage["not_landed"].append(review_data)
                elif len(subscribers) == 0 and len(reviewers) == 0:
                    storage["stale_no_reviewers"].append(review_data)
                else:
                    storage["stale"].append(review_data)

timestamp = datetime.date.today().strftime("%Y_%m_%d")
csvdir = os.path.join(CSV_DIR, timestamp)
Path(csvdir).mkdir(exist_ok=True)

with chdir(csvdir):
    for key, val in db.items():
        dump_html(key, val)

category_template = """
 {total} stale reviews created by {author_string} authors:
  - {total_needs_close} that need to be closed,
  - {total_not_landed} that were accepted but never landed,
  - {total_stale} stale reviews,
  - {total_stale_no_reviewers} stale reviews with no subscribers or reviewers.
"""

category_reports = ""
datedir = os.path.basename(csvdir)
for key, val in db.items():
    total = sum([len(array) for kw, array in val.items() if kw != "author_string"])
    template_args = {
        "total": total,
        "author_string": val["author_string"],
        "total_stale": len(val["stale"]),
        "total_needs_close": len(val["needs_close"]),
        "total_not_landed": len(val["not_landed"]),
        "total_stale_no_reviewers": len(val["stale_no_reviewers"]),
    }
    category_reports += category_template.format(**template_args)
    category_reports += "\n"

report_email = f"""Hello,

This is an automatically generated report for stale Phabricator reviews.

A review is considered stale if it was not modified since {stale_date_thresh}.
Here is a short overview of the currently open reviews:
{category_reports}
Detailed lists are available at <https://people.freebsd.org/~bnovkov/stale_reviews/{datedir}>.
"""

print(report_email)
