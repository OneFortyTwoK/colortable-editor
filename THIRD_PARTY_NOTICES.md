# Third-party notices

Colortable Editor's own code is under the MIT License ([LICENSE](LICENSE)). It is built on
the work of others, which keeps its own licenses, below. The programs on the Releases page
(the Windows `.exe` and the Linux AppImage) carry all of it inside them, with this file,
the license texts in `licenses/` and each Python library's own license files.

## Qt and PySide6

The windows are drawn by Qt 6.11.2, used through PySide6 6.11.2 (Qt for Python, with
shiboken6), under the **GNU Lesser General Public License, version 3 (LGPL-3.0)**. Its
text, and that of the GNU General Public License version 3 it builds on, are in
[licenses/LGPL-3.0-only.txt](licenses/LGPL-3.0-only.txt) and
[licenses/GPL-3.0-only.txt](licenses/GPL-3.0-only.txt).

- Qt's source code: <https://download.qt.io/official_releases/qt/> and <https://code.qt.io/>
- PySide6's source code: <https://download.qt.io/official_releases/QtForPython/> and
  <https://code.qt.io/cgit/pyside/pyside-setup.git/>

Colortable Editor does not change Qt or PySide6. Its own source code and the steps that
build the programs are public, in this repository (`colortable_editor.spec`, `packaging/`
and `.github/workflows/release.yml`), so anyone can rebuild it with a different or changed
Qt and PySide6. In the AppImage, and in the folder PyInstaller makes, the Qt libraries are
separate files that can also be replaced as they are.

## Python libraries

| Library | Version | License |
|---|---|---|
| numpy | 2.5.3 | BSD-3-Clause |
| matplotlib | 3.11.2 | Matplotlib License (based on the PSF License) |
| Pillow | 12.3.0 | MIT-CMU (the historical PIL license) |
| platformdirs | 4.12.1 | MIT |
| defusedxml | 0.7.1 | Python Software Foundation License 2.0 |
| contourpy (for matplotlib) | 1.4.0 | BSD-3-Clause |
| cycler (for matplotlib) | 0.12.1 | BSD-3-Clause |
| fonttools (for matplotlib) | 4.66.0 | MIT |
| kiwisolver (for matplotlib) | 1.5.1 | BSD-3-Clause |
| packaging (for matplotlib) | 26.3 | Apache-2.0 or BSD-2-Clause |
| pyparsing (for matplotlib) | 3.3.3 | MIT |
| python-dateutil (for matplotlib) | 2.9.0.post0 | Apache-2.0 and BSD-3-Clause |
| six (for python-dateutil) | 1.17.0 | MIT |

numpy's binary packages also carry OpenBLAS, LAPACK and the GCC runtime library, and
Pillow's carry image libraries (libjpeg, libpng, FreeType, HarfBuzz and others), each under
the licenses listed in that library's own license file. matplotlib carries the DejaVu and
STIX fonts, under the licenses in its `mpl-data/fonts` folder.

The programs also carry Python 3.12 itself (Python Software Foundation License 2.0), and
are built with PyInstaller 6.22.3 (GPL-2.0-or-later, with an exception that lets the
programs it builds be shared under any license).

## JetBrains Mono, patched by Nerd Fonts

Saved pictures are lettered in JetBrains Mono Nerd Font Mono Bold
(`vendor/fonts/JetBrainsMonoNerdFontMono-Bold.ttf`): JetBrains Mono 2.304, patched by
Nerd Fonts 3.5.1.

- **JetBrains Mono**: Copyright 2020 The JetBrains Mono Project Authors, under the
  **SIL Open Font License 1.1**. JetBrains Mono is a trademark of JetBrains s.r.o.
- **The Nerd Fonts patch**: its code is under the **MIT License**, and the fonts it patches
  stay under the SIL Open Font License 1.1. The icon glyphs it adds come from several icon
  sets, each under its own open license (Font Awesome and Codicons under CC BY 4.0, Material
  Design Icons under Apache 2.0, and others), listed in Nerd Fonts' license audit:
  <https://github.com/ryanoasis/nerd-fonts/blob/v3.5.1/license-audit.md>

### JetBrains Mono's license (`vendor/fonts/LICENSE-JetBrainsMono-OFL.txt`)

```
Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
https://scripts.sil.org/OFL


-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```

### Nerd Fonts' license (`vendor/fonts/LICENSE-NerdFonts.txt`)

```
# Nerd Fonts Licensing

There are various sources used under various licenses:

* Nerd Fonts source fonts, patched fonts, and folders with explict OFL SIL files are licensed under SIL OPEN FONT LICENSE Version 1.1 (see below).
* Nerd Fonts original source code files (such as `.sh`, `.py`, `font-patcher` and others) are licensed under the MIT License (MIT) (see below).
* Many other licenses are present in this project for even more detailed breakdown see: [License Audit](https://github.com/ryanoasis/nerd-fonts/blob/-/license-audit.md).

## Source files not in folders containing an explicit license are using the MIT License (MIT)

The MIT License (MIT)

Copyright (c) 2014 Ryan L McIntyre

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Various Fonts, Patched Fonts, SVGs, Glyph Fonts, and any files in a folder with explicit SIL OFL 1.1 License

Copyright (c) 2014, Ryan L McIntyre (https://ryanlmcintyre.com).

This Font Software is licensed under the SIL Open Font License, Version 1.1.
This license is copied below, and is also available with a FAQ at:
http://scripts.sil.org/OFL

-----------------------------------------------------------
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007
-----------------------------------------------------------

PREAMBLE
The goals of the Open Font License (OFL) are to stimulate worldwide
development of collaborative font projects, to support the font creation
efforts of academic and linguistic communities, and to provide a free and
open framework in which fonts may be shared and improved in partnership
with others.

The OFL allows the licensed fonts to be used, studied, modified and
redistributed freely as long as they are not sold by themselves. The
fonts, including any derivative works, can be bundled, embedded, 
redistributed and/or sold with any software provided that any reserved
names are not used by derivative works. The fonts and derivatives,
however, cannot be released under any other type of license. The
requirement for fonts to remain under this license does not apply
to any document created using the fonts or their derivatives.

DEFINITIONS
"Font Software" refers to the set of files released by the Copyright
Holder(s) under this license and clearly marked as such. This may
include source files, build scripts and documentation.

"Reserved Font Name" refers to any names specified as such after the
copyright statement(s).

"Original Version" refers to the collection of Font Software components as
distributed by the Copyright Holder(s).

"Modified Version" refers to any derivative made by adding to, deleting,
or substituting -- in part or in whole -- any of the components of the
Original Version, by changing formats or by porting the Font Software to a
new environment.

"Author" refers to any designer, engineer, programmer, technical
writer or other person who contributed to the Font Software.

PERMISSION & CONDITIONS
Permission is hereby granted, free of charge, to any person obtaining
a copy of the Font Software, to use, study, copy, merge, embed, modify,
redistribute, and sell modified and unmodified copies of the Font
Software, subject to the following conditions:

1) Neither the Font Software nor any of its individual components,
in Original or Modified Versions, may be sold by itself.

2) Original or Modified Versions of the Font Software may be bundled,
redistributed and/or sold with any software, provided that each copy
contains the above copyright notice and this license. These can be
included either as stand-alone text files, human-readable headers or
in the appropriate machine-readable metadata fields within text or
binary files as long as those fields can be easily viewed by the user.

3) No Modified Version of the Font Software may use the Reserved Font
Name(s) unless explicit written permission is granted by the corresponding
Copyright Holder. This restriction only applies to the primary font name as
presented to the users.

4) The name(s) of the Copyright Holder(s) or the Author(s) of the Font
Software shall not be used to promote, endorse or advertise any
Modified Version, except to acknowledge the contribution(s) of the
Copyright Holder(s) and the Author(s) or with their explicit written
permission.

5) The Font Software, modified or unmodified, in part or in whole,
must be distributed entirely under this license, and must not be
distributed under any other license. The requirement for fonts to
remain under this license does not apply to any document created
using the Font Software.

TERMINATION
This license becomes null and void if any of the above conditions are
not met.

DISCLAIMER
THE FONT SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO ANY WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT
OF COPYRIGHT, PATENT, TRADEMARK, OR OTHER RIGHT. IN NO EVENT SHALL THE
COPYRIGHT HOLDER BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
INCLUDING ANY GENERAL, SPECIAL, INDIRECT, INCIDENTAL, OR CONSEQUENTIAL
DAMAGES, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF THE USE OR INABILITY TO USE THE FONT SOFTWARE OR FROM
OTHER DEALINGS IN THE FONT SOFTWARE.
```
