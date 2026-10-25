# Qt 5.12.8 modules missing from AGNOS 11+

AGNOS 11+ (Ubuntu 24.04) ships a custom Qt 5.12.8 deb (agnos-builder userspace/qtwayland/build-qt-deb.sh)
without Qml, Quick, QuickWidgets, Positioning, Sql, the sqlite driver or the QtQuick.2 QML module.
The 0.9.7 UI (onboarding QML, maps) and libQMapLibre need them. These files are unmodified copies from
the Ubuntu 20.04 (focal) arm64 debs, the same Qt build as AGNOS 10.1 (LGPL-3.0, see Qt licensing):

- libqt5qml5_5.12.8-0ubuntu1, libqt5quick5_5.12.8-0ubuntu1, libqt5quickwidgets5_5.12.8-0ubuntu1
- libqt5positioning5_5.12.8+dfsg-0ubuntu1, qtpositioning5-dev_5.12.8+dfsg-0ubuntu1 (headers)
- libqt5sql5_5.12.8+dfsg-0ubuntu2.1, libqt5sql5-sqlite_5.12.8+dfsg-0ubuntu2.1
- qml-module-qtquick2_5.12.8-0ubuntu1

Source: https://launchpad.net/ubuntu/+archive/primary/+files/<deb>_arm64.deb
Used through SConstruct (LIBPATH/RPATH/CPPPATH) and launch_chffrplus.sh (QML2_IMPORT_PATH, QT_PLUGIN_PATH).

sha256 of the source debs:
```
15eea14a36741bb86f395f7dd8e9d0fd7072d3b0437d40adcd0b54d48d362016  libqt5positioning5_5.12.8+dfsg-0ubuntu1_arm64.deb
ae76b3add10577f052b14a41cdceb0937ca959df4240071e9136987342c9c4e3  libqt5qml5_5.12.8-0ubuntu1_arm64.deb
3cd4b953b28c7c41ac4703ff52107e36dad4868aad76bc60cc8e685439678fa1  libqt5quick5_5.12.8-0ubuntu1_arm64.deb
6f5c89d7d81f23879156c8bac1f4918ec9da902abf8051946c01bf5eedb1cbff  libqt5quickwidgets5_5.12.8-0ubuntu1_arm64.deb
8193246419d618d7744436994fcdf66c9fa7b900bfbb0f63818283550cb2b2d6  libqt5sql5-sqlite_5.12.8+dfsg-0ubuntu2.1_arm64.deb
cf6f7927fc8b1ef085d92747c41c66b7287034a2199acfacd54486ffcd07bdc0  libqt5sql5_5.12.8+dfsg-0ubuntu2.1_arm64.deb
a477f8dd0c3cde743e7e20b7188c35f425cfbcc6e26d1fc7d43c638b9d06d8bf  qml-module-qtquick2_5.12.8-0ubuntu1_arm64.deb
5ddf6c786643e189e3063c686265538bd13aa4ca5d9a8e27820b6ac024963a8a  qtpositioning5-dev_5.12.8+dfsg-0ubuntu1_arm64.deb
```
