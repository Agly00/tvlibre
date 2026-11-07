import QtQuick
import QtQuick.Window

// TV Libre sur la télé : même organisation que l'app iPhone (Direct, Films, Séries, Enregistrés),
// pensée pour la télécommande (flèches, OK, retour). Dessinée en 1920×1080, mise à l'échelle de l'écran.
Window {
    id: win
    visible: true
    width: 1920; height: 1080
    visibility: demoMode ? Window.Windowed : Window.FullScreen
    color: "black"
    title: "TV Libre"

    readonly property color bg: "#0e0e11"
    readonly property color panel: "#1a1a1f"
    readonly property color line: "#2a2a31"
    readonly property color fg: "#f4f4f6"
    readonly property color dim: "#9a9aa3"
    readonly property color accent: "#ff453a"

    // ---------------------------------------------------------------- état
    property string mode: "menu"            // menu | live | vod
    property int page: 0                    // 0 Direct, 1 Films, 2 Séries, 3 Enregistrés
    property string area: "rail"            // rail | groups | grid | rows | episodes
    property int groupIndex: 0
    property int gridIndex: 0
    property int rowIndex: 0
    property var colIndex: ({})
    property var groupChannels: catalog.channels(groupIndex)
    property var liveList: []
    property int liveIndex: 0
    property bool bannerVisible: true
    property var openSeries: null
    property var seriesEpisodes: []
    property int epIndex: 0
    property int epgTick: 0

    readonly property int gridCols: 4
    readonly property var pages: ["Direct", "Films", "Séries", "Enregistrés"]
    readonly property var rows: page === 1 ? catalog.movieRows : page === 2 ? catalog.seriesRows : []

    Connections {
        target: catalog
        function onChanged() { groupChannels = catalog.channels(groupIndex) }
        function onEpgReady(sid) { epgTick++ }
    }
    Connections { target: remote; function onKey(k) { handle(k) } }

    onGroupIndexChanged: { groupChannels = catalog.channels(groupIndex); gridIndex = 0 }

    function col(r) { return colIndex[r] || 0 }
    function setCol(r, c) { var o = Object.assign({}, colIndex); o[r] = c; colIndex = o }
    function epg(id) { epgTick; return catalog.epg(id) }

    // ---------------------------------------------------------------- télécommande
    Item {
        id: keys
        focus: true
        Keys.onPressed: function(e) {
            var map = {}
            map[Qt.Key_Up] = "up"; map[Qt.Key_Down] = "down"; map[Qt.Key_Left] = "left"; map[Qt.Key_Right] = "right"
            map[Qt.Key_Return] = "ok"; map[Qt.Key_Enter] = "ok"; map[Qt.Key_Select] = "ok"; map[Qt.Key_Space] = "play"
            map[Qt.Key_Escape] = "back"; map[Qt.Key_Back] = "back"; map[Qt.Key_Backspace] = "back"; map[Qt.Key_Exit] = "back"
            map[Qt.Key_MediaPlay] = "play"; map[Qt.Key_MediaPause] = "play"; map[Qt.Key_MediaTogglePlayPause] = "play"
            map[Qt.Key_Menu] = "menu"
            if (map[e.key] !== undefined) { handle(map[e.key]); e.accepted = true }
        }
    }

    function handle(k) {
        if (mode === "live") return liveKey(k)
        if (mode === "vod") return vodKey(k)
        menuKey(k)
    }

    function playLive(list, i) {
        liveList = list; liveIndex = i
        player.play(list[i]); mode = "live"; showBanner()
    }

    function liveKey(k) {
        if (k === "up" || k === "down") {
            if (liveList.length < 2) return
            liveIndex = (liveIndex + (k === "up" ? -1 : 1) + liveList.length) % liveList.length
            player.play(liveList[liveIndex]); showBanner()
        } else if (k === "left" || k === "right") {
            var n = catalog.groups.length
            if (n < 2) return
            groupIndex = (groupIndex + (k === "left" ? -1 : 1) + n) % n
            playLive(catalog.channels(groupIndex), 0)
        } else if (k === "ok" || k === "play") {
            bannerVisible ? bannerTimer.stop() : null
            bannerVisible = !bannerVisible
        } else if (k === "back" || k === "menu") {
            mode = "menu"; page = 0; area = "grid"
        }
    }

    function vodKey(k) {
        if (k === "left") { player.seekBy(-10); showBanner() }
        else if (k === "right") { player.seekBy(30); showBanner() }
        else if (k === "ok" || k === "play") { player.togglePause(); showBanner() }
        else if (k === "up" || k === "down") showBanner()
        else if (k === "back" || k === "menu") { mode = "menu"; area = page === 0 ? "rail" : "rows" }
    }

    function menuKey(k) {
        if (area === "episodes") {
            if (k === "up") epIndex = Math.max(0, epIndex - 1)
            else if (k === "down") epIndex = Math.min(seriesEpisodes.length - 1, epIndex + 1)
            else if (k === "ok" && seriesEpisodes.length) { player.play(seriesEpisodes[epIndex]); mode = "vod"; showBanner() }
            else if (k === "back" || k === "left") { openSeries = null; area = "rows" }
            return
        }
        if (area === "rail") {
            if (k === "up") page = Math.max(0, page - 1)
            else if (k === "down") page = Math.min(pages.length - 1, page + 1)
            else if (k === "right" || k === "ok") area = page === 0 ? "groups" : (rows.length ? "rows" : "rail")
            else if (k === "back" && player.playing) mode = player.item.kind === "live" ? "live" : "vod"
            return
        }
        if (area === "groups") {
            var n = catalog.groups.length
            if (k === "left") { if (groupIndex === 0) area = "rail"; else groupIndex-- }
            else if (k === "right") groupIndex = Math.min(n - 1, groupIndex + 1)
            else if (k === "down" || k === "ok") area = "grid"
            else if (k === "back") area = "rail"
            return
        }
        if (area === "grid") {
            var c = groupChannels.length
            if (k === "left") { if (gridIndex % gridCols === 0) area = "rail"; else gridIndex-- }
            else if (k === "right") gridIndex = Math.min(c - 1, gridIndex + 1)
            else if (k === "up") { if (gridIndex < gridCols) area = "groups"; else gridIndex -= gridCols }
            else if (k === "down") gridIndex = Math.min(c - 1, gridIndex + gridCols)
            else if (k === "ok" && c) playLive(groupChannels, gridIndex)
            else if (k === "back") area = "groups"
            return
        }
        if (area === "rows") {
            var r = rows[rowIndex]
            if (!r) { area = "rail"; return }
            if (k === "left") { if (col(rowIndex) === 0) area = "rail"; else setCol(rowIndex, col(rowIndex) - 1) }
            else if (k === "right") setCol(rowIndex, Math.min(r.items.length - 1, col(rowIndex) + 1))
            else if (k === "up") rowIndex = Math.max(0, rowIndex - 1)
            else if (k === "down") rowIndex = Math.min(rows.length - 1, rowIndex + 1)
            else if (k === "ok") {
                var it = r.items[col(rowIndex)]
                if (it.kind === "series") { openSeries = it; seriesEpisodes = catalog.episodes(it.id); epIndex = 0; area = "episodes" }
                else { player.play(it); mode = "vod"; showBanner() }
            }
            else if (k === "back") area = "rail"
        }
    }

    function showBanner() { bannerVisible = true; bannerTimer.restart() }
    Timer { id: bannerTimer; interval: 5000; onTriggered: bannerVisible = false }

    function hms(s) {
        s = Math.max(0, Math.floor(s))
        var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60
        return (h > 0 ? h + ":" + (m < 10 ? "0" : "") : "") + m + ":" + (x < 10 ? "0" : "") + x
    }

    // ---------------------------------------------------------------- dessin (1920×1080 mis à l'échelle)
    Item {
        id: stage
        width: 1920; height: 1080
        anchors.centerIn: parent
        scale: Math.min(win.width / 1920, win.height / 1080)

        // Vidéo (mpv) ; en démonstration, une image fictive
        // (toujours affichée : mpv doit avoir sa surface avant la première lecture)
        Loader {
            anchors.fill: parent
            visible: player.playing || !demoMode
            sourceComponent: demoMode ? demoFrame : videoFrame
        }
        Component { id: videoFrame; Item { Loader { anchors.fill: parent; source: "Video.qml" } } }
        Component {
            id: demoFrame
            Rectangle {
                gradient: Gradient {
                    GradientStop { position: 0; color: Qt.hsla((player.item.hue || 0.6), 0.45, 0.32, 1) }
                    GradientStop { position: 1; color: Qt.hsla((player.item.hue || 0.6) + 0.1, 0.5, 0.12, 1) }
                }
                Text { anchors.centerIn: parent; text: "image de « " + (player.item.name || "") + " »"; color: "#55ffffff"; font.pixelSize: 40 }
            }
        }

        // Écran TV Libre quand rien ne joue
        Image {
            anchors.fill: parent
            source: "ecran.jpg"
            visible: !player.playing && mode === "menu"
            opacity: 0.35
            fillMode: Image.PreserveAspectCrop
        }

        // ------------------------------------------------ menus
        Rectangle {
            anchors.fill: parent
            visible: mode === "menu"
            color: player.playing ? "#cc0e0e11" : bg

            // Rail de gauche
            Rectangle {
                id: rail
                width: 300; height: parent.height
                color: area === "rail" ? "#16161b" : "#101014"
                Behavior on color { ColorAnimation { duration: 150 } }
                Row {
                    x: 40; y: 50; spacing: 16
                    Image { source: "icon.png"; width: 56; height: 56; smooth: true }
                    Text { text: "TV Libre"; color: fg; font.pixelSize: 34; font.weight: Font.DemiBold; anchors.verticalCenter: parent.verticalCenter }
                }
                Column {
                    y: 180; width: parent.width; spacing: 8
                    Repeater {
                        model: pages
                        Rectangle {
                            width: 260; height: 70; x: 20; radius: 14
                            color: page === index ? (area === "rail" ? "#ffffff" : "#2c2c33") : "transparent"
                            Text {
                                x: 26; anchors.verticalCenter: parent.verticalCenter
                                text: modelData; font.pixelSize: 30; font.weight: page === index ? Font.DemiBold : Font.Normal
                                color: page === index && area === "rail" ? "#111" : (page === index ? fg : dim)
                            }
                        }
                    }
                }
                Text {
                    visible: player.playing
                    x: 40; anchors.bottom: parent.bottom; anchors.bottomMargin: 50; width: 230; wrapMode: Text.WordWrap
                    text: "En lecture : " + (player.item.name || "") + "\nRetour pour y revenir"
                    color: dim; font.pixelSize: 20
                }
            }

            Item {
                id: content
                x: rail.width + 40; y: 50
                width: parent.width - x - 40; height: parent.height - 50

                Text {
                    visible: catalog.loading
                    text: "Chargement du catalogue…"; color: dim; font.pixelSize: 28
                }

                // -------- Direct : groupes puis grille de chaînes
                Item {
                    anchors.fill: parent
                    visible: page === 0 && !catalog.loading
                    ListView {
                        id: groupsView
                        width: parent.width; height: 64
                        orientation: ListView.Horizontal; spacing: 14
                        model: catalog.groups
                        currentIndex: groupIndex
                        highlightMoveDuration: 200
                        preferredHighlightBegin: 0; preferredHighlightEnd: width * 0.6
                        highlightRangeMode: ListView.ApplyRange
                        delegate: Rectangle {
                            height: 60; radius: 30; width: gt.implicitWidth + 56
                            color: index === groupIndex ? (area === "groups" ? "#ffffff" : accent) : panel
                            Text {
                                id: gt; anchors.centerIn: parent; text: modelData.name; font.pixelSize: 26
                                font.weight: index === groupIndex ? Font.DemiBold : Font.Normal
                                color: index === groupIndex && area === "groups" ? "#111" : fg
                            }
                        }
                    }
                    GridView {
                        id: grid
                        y: 100; width: parent.width; height: parent.height - 100
                        cellWidth: width / gridCols; cellHeight: 250
                        model: groupChannels
                        currentIndex: gridIndex
                        clip: true
                        highlightMoveDuration: 200
                        preferredHighlightBegin: 0; preferredHighlightEnd: 500
                        highlightRangeMode: GridView.ApplyRange
                        delegate: Item {
                            width: grid.cellWidth; height: grid.cellHeight
                            property bool sel: index === gridIndex && area === "grid"
                            property var e: epg(modelData.id)
                            Rectangle {
                                id: card
                                x: 10; y: 10; width: parent.width - 20; height: 160; radius: 18
                                color: modelData.logo ? "#26262c" : Qt.hsla(modelData.hue || 0.6, 0.35, 0.28, 1)
                                border.width: sel ? 5 : 0; border.color: "white"
                                scale: sel ? 1.05 : 1
                                Behavior on scale { NumberAnimation { duration: 120 } }
                                Image {
                                    anchors.fill: parent; anchors.margins: 26; source: modelData.logo || ""
                                    fillMode: Image.PreserveAspectFit; asynchronous: true; visible: !!modelData.logo
                                }
                                Text {
                                    visible: !modelData.logo; anchors.centerIn: parent
                                    text: modelData.name; color: "white"; font.pixelSize: 34; font.weight: Font.Bold
                                }
                                Rectangle {
                                    visible: (e.progress || 0) > 0
                                    anchors.bottom: parent.bottom; x: 0; height: 6; radius: 3
                                    width: parent.width * (e.progress || 0); color: accent
                                }
                            }
                            Text {
                                x: 14; y: 180; width: parent.width - 28; elide: Text.ElideRight
                                text: (modelData.num ? modelData.num + "  " : "") + modelData.name
                                color: sel ? fg : dim; font.pixelSize: 24; font.weight: Font.DemiBold
                            }
                            Text {
                                x: 14; y: 212; width: parent.width - 28; elide: Text.ElideRight
                                text: e.now || ""; color: dim; font.pixelSize: 21
                            }
                        }
                    }
                }

                // -------- Films / Séries : rangées d'affiches
                ListView {
                    id: rowsView
                    anchors.fill: parent
                    visible: (page === 1 || page === 2) && !catalog.loading
                    model: rows
                    currentIndex: rowIndex
                    spacing: 30; clip: true
                    highlightMoveDuration: 250
                    preferredHighlightBegin: 0; preferredHighlightEnd: 200
                    highlightRangeMode: ListView.ApplyRange
                    delegate: Column {
                        id: rowItem
                        property int r: index
                        width: rowsView.width; spacing: 14
                        Text { text: modelData.title; color: fg; font.pixelSize: 30; font.weight: Font.DemiBold }
                        ListView {
                            width: parent.width; height: 380
                            orientation: ListView.Horizontal; spacing: 24
                            model: modelData.items
                            currentIndex: col(rowItem.r)
                            highlightMoveDuration: 200
                            preferredHighlightBegin: 0; preferredHighlightEnd: width * 0.7
                            highlightRangeMode: ListView.ApplyRange
                            delegate: Item {
                                width: 220; height: 380
                                property bool sel: rowItem.r === rowIndex && index === col(rowItem.r) && area === "rows"
                                Rectangle {
                                    x: 0; y: 10; width: 220; height: 330; radius: 14; clip: true
                                    color: Qt.hsla(modelData.hue || 0.55, 0.4, 0.3, 1)
                                    border.width: sel ? 5 : 0; border.color: "white"
                                    scale: sel ? 1.06 : 1
                                    Behavior on scale { NumberAnimation { duration: 120 } }
                                    Image { anchors.fill: parent; source: modelData.poster || ""; fillMode: Image.PreserveAspectCrop; asynchronous: true }
                                    Text {
                                        visible: !modelData.poster; anchors.fill: parent; anchors.margins: 18
                                        text: modelData.name; color: "white"; wrapMode: Text.WordWrap
                                        font.pixelSize: 28; font.weight: Font.Bold; verticalAlignment: Text.AlignBottom
                                    }
                                    Rectangle {
                                        visible: (modelData.progress || 0) > 0
                                        anchors.bottom: parent.bottom; height: 8; width: parent.width * (modelData.progress || 0); color: accent
                                    }
                                }
                                Text {
                                    y: 350; width: 220; elide: Text.ElideRight; text: modelData.name
                                    color: sel ? fg : dim; font.pixelSize: 21
                                }
                            }
                        }
                    }
                }

                Text {
                    visible: page === 3
                    text: "Enregistrements et films mis de côté sur la clé du Raspberry\n(à brancher sur le relais : liste /rec/list)"
                    color: dim; font.pixelSize: 28
                }
            }

            // -------- Épisodes d'une série
            Rectangle {
                visible: area === "episodes" && openSeries !== null
                anchors.right: parent.right; width: 760; height: parent.height
                color: "#141418"
                Text { x: 50; y: 50; width: 660; elide: Text.ElideRight; text: openSeries ? openSeries.name : ""; color: fg; font.pixelSize: 38; font.weight: Font.Bold }
                ListView {
                    x: 30; y: 130; width: 700; height: parent.height - 170
                    model: seriesEpisodes; currentIndex: epIndex; clip: true; spacing: 8
                    highlightMoveDuration: 150
                    delegate: Rectangle {
                        width: 700; height: 76; radius: 14
                        color: index === epIndex ? "white" : "transparent"
                        Text {
                            x: 24; anchors.verticalCenter: parent.verticalCenter; width: 650; elide: Text.ElideRight
                            text: "S" + modelData.season + " É" + modelData.episode + "   " + modelData.name
                            color: index === epIndex ? "#111" : fg; font.pixelSize: 26
                        }
                    }
                }
            }
        }

        // ------------------------------------------------ bandeau du direct
        Rectangle {
            id: liveBanner
            visible: mode === "live" && bannerVisible
            anchors.bottom: parent.bottom; anchors.bottomMargin: 50
            anchors.horizontalCenter: parent.horizontalCenter
            width: 1700; height: 220; radius: 30
            color: "#e6141418"
            property var ch: liveList[liveIndex] || ({})
            property var e: epg(ch.id || "")
            Rectangle {
                x: 30; y: 30; width: 260; height: 160; radius: 18
                color: liveBanner.ch.logo ? "#26262c" : Qt.hsla(liveBanner.ch.hue || 0.6, 0.35, 0.28, 1)
                Image { anchors.fill: parent; anchors.margins: 22; source: liveBanner.ch.logo || ""; fillMode: Image.PreserveAspectFit }
                Text { visible: !liveBanner.ch.logo; anchors.centerIn: parent; text: liveBanner.ch.name || ""; color: "white"; font.pixelSize: 30; font.weight: Font.Bold }
            }
            Column {
                x: 330; y: 28; width: 1330; spacing: 8
                Row {
                    spacing: 18
                    Text { text: liveBanner.ch.num || ""; color: accent; font.pixelSize: 34; font.weight: Font.Bold }
                    Text { text: liveBanner.ch.name || ""; color: fg; font.pixelSize: 34; font.weight: Font.Bold }
                    Text { text: (catalog.groups[groupIndex] || {}).name || ""; color: dim; font.pixelSize: 24; anchors.bottom: parent.bottom; anchors.bottomMargin: 4 }
                }
                Text { text: liveBanner.e.now || " "; color: fg; font.pixelSize: 30; width: parent.width; elide: Text.ElideRight }
                Item {
                    width: parent.width; height: 26
                    Text { text: liveBanner.e.start || ""; color: dim; font.pixelSize: 20; anchors.verticalCenter: parent.verticalCenter }
                    Rectangle {
                        x: 80; width: parent.width - 160; height: 8; radius: 4; color: line; anchors.verticalCenter: parent.verticalCenter
                        Rectangle { height: parent.height; radius: 4; color: accent; width: parent.width * (liveBanner.e.progress || 0) }
                    }
                    Text { text: liveBanner.e.end || ""; color: dim; font.pixelSize: 20; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter }
                }
                Text { text: liveBanner.e.next ? "Ensuite : " + liveBanner.e.next : ""; color: dim; font.pixelSize: 24 }
            }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 36; anchors.top: parent.top; anchors.topMargin: 30
                text: "▲▼ chaîne   ◀▶ groupe"; color: dim; font.pixelSize: 20
            }
        }

        // ------------------------------------------------ barre des films et épisodes
        Rectangle {
            visible: mode === "vod" && (bannerVisible || player.paused)
            anchors.bottom: parent.bottom; anchors.bottomMargin: 50
            anchors.horizontalCenter: parent.horizontalCenter
            width: 1700; height: 170; radius: 30
            color: "#e6141418"
            Text { x: 40; y: 26; width: 1400; elide: Text.ElideRight; text: player.item.name || ""; color: fg; font.pixelSize: 34; font.weight: Font.Bold }
            Text { anchors.right: parent.right; anchors.rightMargin: 40; y: 30; text: player.paused ? "En pause" : ""; color: accent; font.pixelSize: 26; font.weight: Font.DemiBold }
            Item {
                x: 40; y: 96; width: parent.width - 80; height: 40
                Text { text: hms(player.position); color: dim; font.pixelSize: 24; anchors.verticalCenter: parent.verticalCenter }
                Rectangle {
                    x: 110; width: parent.width - 220; height: 10; radius: 5; color: line; anchors.verticalCenter: parent.verticalCenter
                    Rectangle { height: parent.height; radius: 5; color: accent; width: parent.width * (player.duration > 0 ? player.position / player.duration : 0) }
                }
                Text { text: hms(player.duration); color: dim; font.pixelSize: 24; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter }
            }
        }
    }

    Component.onCompleted: {
        if (startPage === "films") { page = 1; area = "rows" }
        else if (startPage === "series") { page = 2; area = "rows" }
        else if (startPage === "lecture") { gridDemo.start() }
        else { page = 0; area = "grid" }
    }
    Timer {
        id: gridDemo; interval: 800
        onTriggered: { if (groupChannels.length) { gridIndex = 2; playLive(groupChannels, 2) } }
    }
}
