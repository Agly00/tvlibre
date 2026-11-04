// Données intégrées : chaînes principales (ordre d'affichage, clés de fusion) et listes de base.

export const COUNTRIES = [
  { id: 'fr', label: 'France (TNT)', iso: 'FR' },
  { id: 'ch', label: 'Suisse', iso: 'CH' },
  { id: 'other', label: 'Autres', iso: null },
];

// Alias séparés par "|" (tvg-id ou nom, normalisés)
export const MAIN = {
  fr: [
    'tf1', 'france2', 'france3', 'france4', 'france5', 'm6', 'arte|artefrance',
    'lcp|lcpassembleenationale|lcpan', 'publicsenat', 'w9', 'tmc', 'tfx', 'nrj12',
    'lci', 'franceinfo|franceinfotv|franceinfo27', 'bfmtv', 'cnews', 'cstar', 'gulli',
    'tf1seriesfilms', 'lequipe|lequipetv', '6ter', 'rmcstory', 'rmcdecouverte',
    'cherie25', 't18', 'novo19', 'france24|france24francais', 'tv5mondeeurope|tv5monde',
    'tv5mondefbs|tv5mondefrancebelgiqueswitzerland', 'tv5mondeinfo',
    'bfmbusiness', 'euronewsfrancais|euronewsfrench|euronews',
  ],
  ch: [
    'rts1|rtsun', 'rts2|rtsdeux', 'rtsinfo', 'srf1|srfeins', 'srfzwei|srf2', 'srfinfo',
    'rsila1|la1', 'rsila2|la2', 'lemanbleu', 'latele|latelevaudfribourg', 'canal9|kanal9',
    'carac|carac1', 'tvm3', 'teleticino', 'telebarn|telebaern', 'telezuri|telezueri', 'telebasel',
    'telem1', 'tele1', 'tvo', 'tvsudostschweiz|tvso', 'telebielingue', 'canalalpha',
  ],
};

// Flux publiés par les diffuseurs eux-mêmes. Ⓖ = géobloqué connu.
export const OFFICIAL_M3U = `#EXTM3U
#EXTINF:-1 tvg-id="Arte.fr" group-title="FR",Arte Ⓖ
https://artesimulcast.akamaized.net/hls/live/2031003/artelive_fr/index.m3u8
#EXTINF:-1 tvg-id="TF1.fr" group-title="FR",TF1 Ⓖ
https://tf1-hls-live-ssl.tf1.fr/tf1/1/hls/live_2328.m3u8
#EXTINF:-1 tvg-id="NRJ12.fr" group-title="FR",NRJ 12
https://nrj12.nrjaudio.fm/hls/live/2038374/nrj_12/master.m3u8
#EXTINF:-1 tvg-id="BFMTV.fr" group-title="FR",BFM TV
https://bfmtvalive1-a.akamaihd.net/r8ef15893bf3d4c2db0105218bdfe87f4/eu-central-1/876450610001/playlist.m3u8
#EXTINF:-1 tvg-id="Gulli.fr" group-title="FR",Gulli
https://d13anarbtxy8c5.cloudfront.net/6play/short/clr/gulli/sdindex.m3u8
#EXTINF:-1 tvg-id="TV5MondeEurope.fr" group-title="FR",TV5Monde Europe
https://ott.tv5monde.com/Content/HLS/Live/channel(europe)/index.m3u8
#EXTINF:-1 tvg-id="TV5MondeFBS.fr" group-title="FR",TV5Monde FBS
https://ott.tv5monde.com/Content/HLS/Live/channel(fbs)/index.m3u8
#EXTINF:-1 tvg-id="TV5MondeInfo.fr" group-title="FR",TV5Monde Info
https://ott.tv5monde.com/Content/HLS/Live/channel(info)/index.m3u8
#EXTINF:-1 tvg-id="CGTNFrancais.cn" group-title="FR",CGTN Français
https://news.cgtn.com/resource/live/french/cgtn-f.m3u8
#EXTINF:-1 tvg-id="TVM3.ch" group-title="CH",TVM 3
http://livevideo.infomaniak.com/streaming/livecast/tvm3/playlist.m3u8
#EXTINF:-1 tvg-id="Teleticino.ch" group-title="CH",Teleticino
https://vstream-cdn.ch/hls/teleticino_720p/index.m3u8
#EXTINF:-1 tvg-id="TVO.ch" group-title="CH",TVO
https://cdnapisec.kaltura.com/p/1719221/sp/171922100/playManifest/entryId/1_t5h46v64/format/applehttp/protocol/https/a.m3u8
`;

// Sélection triée : TV locales suisses, RTS, chaînes gratuites en français.
export const SELECTION_M3U = `#EXTM3U
#EXTINF:-1 tvg-id="RTSInfo.ch" group-title="CH",RTS Info Ⓖ
https://rtsinfo-d.akamaized.net/out/v1/2b7ae2e1ba3f43c6aba15bced153baf5/index.m3u8
#EXTINF:-1 tvg-id="Couleur3.ch" group-title="CH",Couleur 3 Ⓖ
https://visualradio-rts-couleur3-d.akamaized.net/out/v1/6a1472634ad745f59a9e63ee8adbbc00/index.m3u8
#EXTINF:-1 tvg-id="LemanBleu.ch" group-title="CH",Léman Bleu (1080p)
http://livevideo.infomaniak.com/streaming/livecast/naxoo/playlist.m3u8
#EXTINF:-1 tvg-id="Canal9.ch" group-title="CH",Canal 9 (1080p)
https://livehd.vedge.infomaniak.com/livecast/livehd/master.m3u8
#EXTINF:-1 tvg-id="RhoneTV.ch" group-title="CH",Rhône TV (1080p) Ⓖ
https://edge14.vedge.infomaniak.com/livecast/ik:rhonetv/manifest.m3u8
#EXTINF:-1 tvg-id="CanalAlphaNeuchatel.ch" group-title="CH",Canal Alpha Neuchâtel (1080p)
https://canalalpha.vedge.infomaniak.com/livecast/ik:canalalpha/playlist.m3u8
#EXTINF:-1 tvg-id="CanalAlphaJura.ch" group-title="CH",Canal Alpha Jura (1080p)
https://canalalphaju.vedge.infomaniak.com/livecast/ik:canalalphaju/playlist.m3u8
#EXTINF:-1 tvg-id="Carac1.ch" group-title="CH",Carac 1 (1080p)
https://edge13.vedge.infomaniak.com/livecast/ik:event/manifest.m3u8
#EXTINF:-1 tvg-id="Carac2.ch" group-title="CH",Carac 2 (1080p)
https://onefmhd.vedge.infomaniak.com/livecast/ik:onefmhd/manifest.m3u8
#EXTINF:-1 tvg-id="Carac3.ch" group-title="CH",Carac 3 (1080p)
https://lfmhd.vedge.infomaniak.com/livecast/lfmhd/playlist.m3u8
#EXTINF:-1 tvg-id="Carac4.ch" group-title="CH",Carac 4 (1080p)
https://edge17.vedge.infomaniak.com/livecast/ik:compack_media_1/playlist.m3u8
#EXTINF:-1 tvg-id="Carac5.ch" group-title="CH",Carac 5
https://lfmmd.vedge.infomaniak.com/livecast/ik:carac5/manifest.m3u8
#EXTINF:-1 tvg-id="TeleTicino.ch" group-title="CH",Teleticino (720p)
https://vstream-cdn.ch/hls/teleticino.m3u8
#EXTINF:-1 tvg-id="TeleM1.ch" group-title="CH",Tele M1 (720p)
https://cdnapisec.kaltura.com/p/1719221/sp/171922100/playManifest/entryId/1_ljzy3evp/format/applehttp/protocol/https/a.m3u8
#EXTINF:-1 tvg-id="Meteonews.ch" group-title="CH",Meteonews (1080p)
https://streaming.meteonews.net/hls/stream.m3u8
#EXTINF:-1 tvg-id="TV5MondeFBS.fr" group-title="FR",TV5Monde FBS (1080p) Ⓖ
https://ott.tv5monde.com/Content/HLS/Live/channel(fbs)/variant.m3u8
#EXTINF:-1 tvg-id="KTO.fr" group-title="FR",KTO
https://live-kto.akamaized.net/hls/live/2033284/KTO/master.m3u8
#EXTINF:-1 tvg-id="CineNanar.fr" group-title="FR",Ciné Nanar (1080p)
https://zylo-cinenanar-rakuten.amagi.tv/playlist.m3u8
#EXTINF:-1 tvg-id="CineWestern.fr" group-title="FR",Ciné Western (1080p)
https://amg00711-zylo-amg00711c8-rakuten-uk-2686.playouts.now.amagi.tv/playlist/amg00711-zylofast-cinewestern-rakutenuk/playlist.m3u8
#EXTINF:-1 tvg-id="EmotionL.fr" group-title="FR",Émotion'L (1080p)
https://rakutenaa-zylo-emotional-rakuten-r1zkm.amagi.tv/playlist/rakutenAA-zylo-emotional-rakuten/playlist.m3u8
#EXTINF:-1 tvg-id="IntoCrime.fr" group-title="FR",Into Crime (1080p)
https://amg00711-zylo-amg00711c10-rakuten-fr-6731.playouts.now.amagi.tv/playlist.m3u8
#EXTINF:-1 tvg-id="FrequenceNovelas.fr" group-title="FR",Fréquence Novelas (1080p)
https://d20oth1416y9a1.cloudfront.net/playlist/amg00711-zylofast-frequencenovelas-rakutenus/playlist.m3u8
#EXTINF:-1 tvg-id="ScreamIN.fr" group-title="FR",ScreamIN (1080p)
https://rakutenaa-zylo-screamin-rakuten-p11ej.amagi.tv/playlist/rakutenAA-zylo-screamin-rakuten/playlist.m3u8
#EXTINF:-1 tvg-id="FIFAPlusFrench.fr" group-title="FR",FIFA+ (720p)
https://37b4c228.wurl.com/master/f36d25e7e52f1ba8d7e56eb859c636563214f541/UmFrdXRlblRWLWZyX0ZJRkFQbHVzRnJlbmNoX0hMUw/playlist.m3u8
#EXTINF:-1 tvg-id="ZeeOneFrench.fr" group-title="FR",Zee One (720p)
https://7689426c.wurl.com/master/f36d25e7e52f1ba8d7e56eb859c636563214f541/U2Ftc3VuZy1mcl9aZWVNYWdpY19ITFM/playlist.m3u8
#EXTINF:-1 tvg-id="ClubbingTVFrance.fr" group-title="FR",Clubbing TV
https://d1j2csarxnwazk.cloudfront.net/v1/master/3722c60a815c199d9c0ef36c5b73da68a62b09d1/cc-uze1m6xh4fiyr-ssai-prd/master.m3u8
`;

// Listes de base. kind : text (intégrée), remote (URL). Les groupes FR/CH des listes intégrées
// et de Free-TV donnent le pays ; iptv-org a une liste par pays.
export const BUILTIN_SOURCES = [
  { id: 'builtin.officiel', name: 'Flux officiels des diffuseurs', kind: 'text', text: OFFICIAL_M3U,
    groups: { FR: 'fr', CH: 'ch' }, builtin: true },
  { id: 'builtin.selection', name: 'Sélection légale CH / FR', kind: 'text', text: SELECTION_M3U,
    groups: { FR: 'fr', CH: 'ch' }, builtin: true, pinned: true },
  { id: 'builtin.iptvorg.fr', name: 'iptv-org France', kind: 'remote',
    url: 'https://iptv-org.github.io/iptv/countries/fr.m3u', defaultCountry: 'fr', builtin: true },
  { id: 'builtin.iptvorg.ch', name: 'iptv-org Suisse', kind: 'remote',
    url: 'https://iptv-org.github.io/iptv/countries/ch.m3u', defaultCountry: 'ch', builtin: true },
  { id: 'builtin.freetv', name: 'Free-TV (France, Suisse)', kind: 'remote',
    url: 'https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8',
    groups: { France: 'fr', Switzerland: 'ch', Suisse: 'ch' }, builtin: true },
];
