# Stale phabricator reviews script
This script uses Phabricator's Conduit API to look for open reviews on https://reviews.freebsd.org and compile a list of stale reviews.
Running it will produce a folder with containing an HTML for each stale review category.
The script will also print out a brief `sendmail`-ready report.

## Requirements
- A `src` git repo must be present in the script's working directory, stored in the `freebsd-src` folder
  - N.B: The repository should be checkout out to an up-to-date `main` branch before running the script
- The `arcanist` utility must be installed
