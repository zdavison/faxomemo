# Third-Party Notices

faxomemo is under the [MIT License](LICENSE). This file lists the third-party parts that faxomemo includes or uses, and the license of each part.

## Files in this repository

| Part | Location | License |
|---|---|---|
| PC-1 (paper-console) by Travis Miller | Most of the code | MIT |
| DejaVu Sans font | `app/data/fonts/` | Bitstream Vera and public domain. Read `app/data/fonts/DejaVuSans-LICENSE.txt`. |
| IBM Plex Sans and IBM Plex Mono fonts | `web/public/fonts/`, `web/dist/fonts/` | SIL Open Font License 1.1. Read the `OFL.txt` file in each font folder. |
| Phosphor Icons | `icons/regular/` | MIT, Copyright (c) 2023 Phosphor Icons |
| GeoNames city data | `app/data/geonames_cities.csv` | CC BY 4.0. The data comes from [GeoNames](https://www.geonames.org/). |
| Test recipe based on the calibre `wsj_free.recipe` | `testing/newsstand_fixtures/wsj_trimmed.recipe` | GPL-3.0. Read the header of the file. |

The test recipe is a test fixture only. The faxomemo app does not run this file. `scripts/release_build.py` copies the `testing/` folder into release artifacts, so release artifacts include this file.

## Python dependencies

`pip` installs these libraries from `requirements.txt`. The faxomemo repository and release artifacts do not include copies of them.

Most dependencies use MIT, BSD, Apache-2.0, PSF or MPL-2.0 licenses. These dependencies use the GPL:

| Library | License | Used by |
|---|---|---|
| [python-chess](https://github.com/niklasf/python-chess) (`chess`) | GPL-3.0-or-later | The chess puzzle module |
| [html2text](https://github.com/Alir3z4/html2text) | GPL-3.0-or-later | `xword-dl`, for the crossword module |
| [Unidecode](https://github.com/avian2/unidecode) | GPL-2.0-or-later | `xword-dl`, for the crossword module |

The MIT License is compatible with the GPL. Thus, faxomemo can use these libraries. If you distribute faxomemo together with these libraries, for example in a device image, the combined work must follow the terms of the GPL.

## Content that faxomemo downloads at runtime

faxomemo does not include these parts. faxomemo downloads them when you use the related feature.

| Part | Feature | License |
|---|---|---|
| [phomymo](https://github.com/transcriptionstream/phomymo) | Label designer (`scripts/fetch_label_designer.sh`) | The phomymo README says MIT. The phomymo `package.json` says ISC. The phomymo repository has no LICENSE file. |
| Recipes from [calibre](https://github.com/kovidgoyal/calibre) | Newsstand module | GPL-3.0 |
| Daily puzzle from [Lichess](https://lichess.org/) | Chess puzzle module | CC0 |
