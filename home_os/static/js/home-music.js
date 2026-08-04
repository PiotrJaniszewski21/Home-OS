(() => {
    "use strict";

    const root = document.getElementById("home-music-app");
    if (!root) return;

    const icons = {
        play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7V5Z"/></svg>',
        pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7V5Zm6 0h4v14h-4V5Z"/></svg>',
        more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>',
        heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 0 0 0-7.78Z"/></svg>',
        radio: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 9h14v11H5V9Zm3-4 8 4M8 14h.01M11 14h5M8 17h.01M11 17h5"/></svg>',
        library: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v16H5V4Zm4 0v16M5 9h4"/></svg>',
        history: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2"/></svg>',
        album: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/></svg>',
        plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>'
    };

    const state = {
        view: "listen",
        home: {suggested_songs: [], suggested_albums: [], new_releases: []},
        recommendations: [],
        history: [],
        loved: [],
        albums: [],
        playlists: [],
        radio: [],
        radioResults: [],
        radioFavourites: new Set(JSON.parse(localStorage.getItem("homeMusicRadioFavourites") || "[]")),
        search: {query: "", tracks: [], artists: [], albums: []},
        searchController: null,
        current: null,
        currentRadio: null,
        queue: [],
        previous: [],
        playbackDuration: 0,
        historyRecorded: false,
        transitionPending: false,
        activePlaylist: null
    };

    const content = document.getElementById("hm-content");
    const audio = document.getElementById("hm-audio");
    const overlay = document.getElementById("hm-overlay");
    const overlayPanel = document.getElementById("hm-overlay-panel");
    const menu = document.getElementById("hm-menu");
    const menuPanel = document.getElementById("hm-menu-panel");
    const searchInput = document.getElementById("hm-search-input");
    const historyPanel = document.getElementById("hm-search-history");
    const csrfToken = root.dataset.csrfToken;

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function button(className, label, action) {
        const node = element("button", className, label);
        node.type = "button";
        if (action) node.addEventListener("click", action);
        return node;
    }

    function iconButton(icon, label, action, className = "") {
        const node = button(`hm-icon-button ${className}`.trim(), "", action);
        node.setAttribute("aria-label", label);
        node.innerHTML = icon;
        return node;
    }

    function fallbackArtwork(label, kind = "music") {
        const firstCharacter = String(label || "").match(/[A-Za-z0-9]/)?.[0]?.toUpperCase();
        const mark = kind === "radio" ? "●" : firstCharacter || "♪";
        const palettes = {
            artist: ["#ff6680", "#8a63d9"],
            radio: ["#ef476f", "#6c5ce7"],
            music: ["#ff5a78", "#667eea"]
        };
        const [start, end] = palettes[kind] || palettes.music;
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="${start}"/><stop offset="1" stop-color="${end}"/></linearGradient></defs><rect width="512" height="512" rx="${kind === "artist" ? 256 : 76}" fill="url(#g)"/><circle cx="408" cy="104" r="120" fill="white" opacity=".08"/><text x="256" y="302" text-anchor="middle" fill="white" font-family="-apple-system,BlinkMacSystemFont,Arial,sans-serif" font-size="190" font-weight="700">${mark}</text></svg>`;
        return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`;
    }

    function image(url, className, alt = "", fallbackKind = "music") {
        const node = element("img", className);
        node.alt = alt;
        node.loading = "lazy";
        const fallback = fallbackArtwork(alt, fallbackKind);
        node.src = url || fallback;
        node.addEventListener("error", () => {
            node.src = fallback;
        }, {once: true});
        return node;
    }

    function showAlert(message) {
        const alert = document.getElementById("hm-alert");
        alert.textContent = message || "";
        alert.classList.toggle("hidden", !message);
    }

    async function request(path, options = {}) {
        const headers = {
            Accept: "application/json",
            ...(options.body ? {"Content-Type": "application/json", "X-CSRFToken": csrfToken} : {}),
            ...(options.headers || {})
        };
        const response = await fetch(path, {...options, headers});
        const payload = await response.json().catch(() => ({ok: false, error: `Request failed (${response.status})`}));
        if (!response.ok || payload.ok === false) throw new Error(payload.error || `Request failed (${response.status})`);
        return payload.data;
    }

    function loading(label) {
        return element("div", "hm-loading", label);
    }

    function empty(label) {
        return element("div", "hm-empty", label);
    }

    function pageHeader(title, subtitle, actions = []) {
        const header = element("header", "hm-page-header");
        const copy = element("div");
        copy.append(element("h1", "", title), element("p", "", subtitle));
        const actionBar = element("div", "hm-page-actions");
        actionBar.append(...actions);
        header.append(copy, actionBar);
        return header;
    }

    function section(title, subtitle, body, action) {
        const wrapper = element("section", "hm-section");
        const heading = element("div", "hm-section-heading");
        const copy = element("div");
        copy.append(element("h2", "", title));
        if (subtitle) copy.append(element("p", "", subtitle));
        heading.append(copy);
        if (action) heading.append(action);
        wrapper.append(heading, body);
        return wrapper;
    }

    function releaseCard(release, artist = false) {
        const card = element("article", `hm-release${artist ? " hm-artist-card" : ""}`);
        card.tabIndex = 0;
        card.append(
            image(
                release.thumbnail,
                "hm-release-art",
                release.title || release.name || "",
                artist ? "artist" : "music"
            ),
            element("h3", "", release.title || release.name || "Unknown"),
            element("p", "", release.artist || [release.type, release.year].filter(Boolean).join(" · "))
        );
        const open = () => artist ? openArtist(release.id) : openAlbum(release.id);
        card.addEventListener("click", open);
        card.addEventListener("keydown", event => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                open();
            }
        });
        return card;
    }

    function releaseRail(releases, artist = false) {
        if (!releases.length) return empty("Nothing to show here yet.");
        const rail = element("div", "hm-rail");
        rail.append(...releases.map(item => releaseCard(item, artist)));
        return rail;
    }

    function trackRow(track, context, options = {}) {
        const row = element("article", "hm-track-row");
        const play = button("hm-track-play", "", () => playTrack(track, context, context.findIndex(item => item.id === track.id)));
        play.setAttribute("aria-label", `Play ${track.title}`);
        play.append(image(track.thumbnail, "", track.title));
        const copy = button("hm-track-copy", "", () => playTrack(track, context, context.findIndex(item => item.id === track.id)));
        copy.append(element("strong", "", track.title), element("span", "", track.artist));
        const actions = element("div", "hm-track-actions");
        if (track.liked === true) actions.append(iconButton(icons.heart, "Loved", () => toggleTrackLike(track), "loved"));
        actions.append(iconButton(icons.more, `More options for ${track.title}`, () => openTrackMenu(track, options)));
        row.append(play, copy, actions);
        return row;
    }

    function trackList(tracks, message, options = {}) {
        if (!tracks.length) return empty(message);
        const list = element("div", "hm-track-list");
        list.append(...tracks.map(track => trackRow(track, tracks, options)));
        return list;
    }

    function setView(view) {
        state.view = view;
        document.querySelectorAll(".hm-nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view));
        renderCurrentView();
        content.focus({preventScroll: true});
        window.scrollTo({top: 0, behavior: "smooth"});
    }

    function renderCurrentView() {
        if (state.view === "listen") renderListenNow();
        else if (state.view === "search") renderSearch();
        else if (state.view === "radio") renderRadio();
        else if (state.view === "library") renderLibrary();
        else if (state.view === "albums") renderAlbums();
        else if (state.view === "playlists") renderPlaylists();
        else if (state.view === "loved") renderCollection("Loved Songs", "Songs you have marked as favourites.", state.loved);
        else if (state.view === "history") renderCollection("Recently Played", "Your listening history follows you across devices.", state.history);
    }

    function renderListenNow() {
        content.replaceChildren(pageHeader("Listen Now", "Music shaped by what you play and love."));
        const songs = state.home.suggested_songs || [];
        if (songs.length) {
            const hero = element("article", "hm-station-hero");
            hero.append(image(songs[0].thumbnail, "", songs[0].title));
            const copy = element("div", "hm-hero-copy");
            copy.append(
                element("small", "", "MADE FOR YOU"),
                element("h2", "", "Your Personal Station"),
                element("p", "", `Starting with ${songs[0].title} · ${songs[0].artist}`),
                button("hm-hero-play", "Play", event => {
                    event.stopPropagation();
                    playTrack(songs[0], songs, 0);
                })
            );
            hero.append(copy);
            hero.addEventListener("click", () => playTrack(songs[0], songs, 0));
            content.append(hero);
        } else {
            const welcome = element("div", "hm-empty hm-empty-welcome");
            const symbol = element("span", "hm-empty-symbol");
            symbol.innerHTML = icons.play;
            welcome.append(
                symbol,
                element("h2", "", "Start your HomeMusic"),
                element("p", "", "Search for a song or artist. Your recommendations will grow as you listen and love music."),
                button("hm-button primary", "Search Music", () => {
                    searchInput.focus();
                    searchInput.scrollIntoView({behavior: "smooth", block: "center"});
                })
            );
            content.append(welcome);
        }
        if (state.home.suggested_albums?.length) {
            content.append(section("Albums for You", "Picked from artists and songs you return to.", releaseRail(state.home.suggested_albums)));
        }
        if (state.home.new_releases?.length) {
            content.append(section("New Releases", "The latest from artists you listen to.", releaseRail(state.home.new_releases)));
        }
        if (songs.length) {
            content.append(section("Songs for You", "A fresh queue shaped by your listening.", trackList(songs.slice(0, 15), "")));
        }
        if (state.history.length) {
            content.append(section("Recently Played", "Pick up where you left off.", releaseRail(state.history.slice(0, 12))));
        }
    }

    function recentSearches() {
        return JSON.parse(localStorage.getItem("homeMusicSearchHistory") || "[]");
    }

    function saveSearch(term) {
        const values = recentSearches().filter(item => item.toLowerCase() !== term.toLowerCase());
        values.unshift(term);
        localStorage.setItem("homeMusicSearchHistory", JSON.stringify(values.slice(0, 12)));
    }

    function uniqueByID(items) {
        const seen = new Set();
        return items.filter(item => {
            if (!item?.id || seen.has(item.id)) return false;
            seen.add(item.id);
            return true;
        });
    }

    function renderSearchHistory() {
        const values = recentSearches();
        historyPanel.replaceChildren();
        if (!values.length) {
            historyPanel.classList.add("hidden");
            return;
        }
        const heading = element("div", "hm-search-history-header");
        heading.append(element("span", "", "Recently Searched"), button("hm-text-button", "Clear", event => {
            event.stopPropagation();
            localStorage.removeItem("homeMusicSearchHistory");
            renderSearchHistory();
            renderSearch();
        }));
        historyPanel.append(heading);
        values.forEach(term => historyPanel.append(button("hm-history-term", term, () => {
            searchInput.value = term;
            historyPanel.classList.add("hidden");
            searchMusic(term);
        })));
        historyPanel.classList.remove("hidden");
    }

    function renderSearch() {
        content.replaceChildren(pageHeader("Search", "Find artists, albums and songs across YouTube Music."));
        if (!state.search.query) {
            const values = recentSearches();
            const body = element("div", "hm-track-list");
            if (!values.length) {
                content.append(empty("Search for an artist, album or song."));
                return;
            }
            values.forEach(term => body.append(button("hm-history-term", term, () => {
                searchInput.value = term;
                searchMusic(term);
            })));
            content.append(section("Recently Searched", "", body, button("hm-text-button", "Clear", () => {
                localStorage.removeItem("homeMusicSearchHistory");
                renderSearch();
            })));
            return;
        }
        if (state.search.artists.length) content.append(section("Artists", "", releaseRail(state.search.artists, true)));
        if (state.search.albums.length) content.append(section("Albums", "", releaseRail(state.search.albums)));
        if (state.search.tracks.length) content.append(section("Songs", "", trackList(state.search.tracks, "")));
        if (!state.search.tracks.length && !state.search.artists.length && !state.search.albums.length) {
            content.append(empty(`No results found for “${state.search.query}”.`));
        }
    }

    async function searchMusic(query) {
        const term = query.trim();
        if (!term) return;
        if (state.searchController) state.searchController.abort();
        state.searchController = new AbortController();
        state.search = {query: term, tracks: [], artists: [], albums: []};
        saveSearch(term);
        setView("search");
        content.append(loading(`Searching for “${term}”…`));
        showAlert("");
        try {
            const encoded = encodeURIComponent(term);
            const options = {signal: state.searchController.signal};
            const [tracks, artists, albums] = await Promise.all([
                request(`/api/music/search?q=${encoded}&limit=30`, options),
                request(`/api/music/search/artists?q=${encoded}&limit=10`, options),
                request(`/api/music/search/albums?q=${encoded}&limit=16`, options)
            ]);
            state.search = {
                query: term,
                tracks: uniqueByID(tracks),
                artists: uniqueByID(artists),
                albums: uniqueByID(albums)
            };
            renderSearch();
        } catch (error) {
            if (error.name !== "AbortError") {
                showAlert(error.message);
                renderSearch();
            }
        }
    }

    function libraryCard(title, detail, icon, view, color) {
        const card = button("hm-library-card", "", () => setView(view));
        card.style.setProperty("--card-color", color);
        card.innerHTML = icon;
        card.append(element("h2", "", title), element("p", "", detail));
        return card;
    }

    function renderLibrary() {
        const create = button("hm-button primary", "New Playlist", createPlaylist);
        content.replaceChildren(pageHeader("Library", "Your saved music, playlists and listening history.", [create]));
        const grid = element("div", "hm-library-grid");
        grid.append(
            libraryCard("Loved Songs", `${state.loved.length} songs`, icons.heart, "loved", "rgba(250,45,85,.28)"),
            libraryCard("Albums", `${state.albums.length} saved`, icons.album, "albums", "rgba(92,105,230,.24)"),
            libraryCard("Playlists", `${state.playlists.length} playlists`, icons.library, "playlists", "rgba(58,176,119,.25)"),
            libraryCard("Recently Played", `${state.history.length} songs`, icons.history, "history", "rgba(56,142,235,.25)")
        );
        content.append(grid);
        if (state.albums.length) content.append(section("Recently Added Albums", "", releaseRail(state.albums.slice(0, 10))));
        if (state.playlists.length) content.append(section("Your Playlists", "", playlistGrid(state.playlists.slice(0, 8))));
    }

    function renderAlbums() {
        content.replaceChildren(pageHeader("Albums", "Albums saved to your HomeMusic library."));
        if (!state.albums.length) {
            content.append(empty("Albums you add to your library appear here."));
            return;
        }
        const grid = element("div", "hm-playlist-grid");
        grid.append(...state.albums.map(album => releaseCard(album)));
        content.append(grid);
    }

    function playlistArtwork(playlist) {
        const art = element("div", "hm-playlist-art");
        (playlist.artwork || []).slice(0, 4).forEach(url => art.append(image(url, "", playlist.name)));
        if (!art.children.length) {
            const placeholder = element("span");
            placeholder.innerHTML = icons.library;
            art.append(placeholder);
        }
        return art;
    }

    function playlistGrid(playlists) {
        const grid = element("div", "hm-playlist-grid");
        playlists.forEach(playlist => {
            const card = element("article", "hm-playlist-card");
            card.tabIndex = 0;
            card.append(playlistArtwork(playlist), element("h3", "", playlist.name), element("p", "", `${playlist.track_count} songs`));
            card.addEventListener("click", () => openPlaylist(playlist.id));
            grid.append(card);
        });
        return grid;
    }

    function renderPlaylists() {
        content.replaceChildren(pageHeader("Playlists", "Collections that stay with your Home OS account.", [
            button("hm-button primary", "New Playlist", createPlaylist)
        ]));
        content.append(state.playlists.length ? playlistGrid(state.playlists) : empty("Create your first playlist to start collecting songs."));
    }

    function renderCollection(title, subtitle, tracks) {
        const actions = tracks.length ? [
            button("hm-button primary", "Play", () => playTrack(tracks[0], tracks, 0)),
            button("hm-button", "Shuffle", () => {
                const shuffled = [...tracks].sort(() => Math.random() - .5);
                playTrack(shuffled[0], shuffled, 0);
            })
        ] : [];
        content.replaceChildren(pageHeader(title, subtitle, actions), trackList(tracks, "No songs here yet."));
    }

    function radioCard(station) {
        const card = element("article", "hm-radio-card");
        const artwork = station.artwork?.startsWith("https://") ? image(station.artwork, "", station.name, "radio") : element("span", "hm-radio-placeholder");
        if (!station.artwork?.startsWith("https://")) artwork.innerHTML = icons.radio;
        const copy = element("div");
        copy.append(element("strong", "", station.name), element("span", "", [station.tags?.[0], station.country].filter(Boolean).join(" · ")), element("span", "hm-live", `LIVE${station.bitrate ? ` · ${station.bitrate} kbps` : ""}`));
        const favourite = iconButton(icons.heart, "Favourite station", event => {
            event.stopPropagation();
            toggleRadioFavourite(station);
        }, state.radioFavourites.has(station.id) ? "loved" : "");
        card.append(artwork, copy, favourite);
        card.addEventListener("click", () => playRadio(station));
        return card;
    }

    function renderRadio() {
        content.replaceChildren(pageHeader("Radio", "Live stations from the UK and around the world."));
        const form = element("form", "hm-radio-search");
        const input = element("input");
        input.type = "search";
        input.placeholder = "Search stations and genres";
        const submit = button("hm-button primary", "Search");
        submit.type = "submit";
        form.append(input, submit);
        form.addEventListener("submit", event => {
            event.preventDefault();
            searchRadio(input.value);
        });
        content.append(form);
        const favourites = state.radio.filter(station => state.radioFavourites.has(station.id));
        if (favourites.length) {
            const grid = element("div", "hm-radio-grid");
            grid.append(...favourites.map(radioCard));
            content.append(section("Favourites", "Stations saved in this browser.", grid));
        }
        const stations = state.radioResults.length ? state.radioResults : state.radio;
        const grid = element("div", "hm-radio-grid");
        grid.append(...stations.map(radioCard));
        content.append(section(state.radioResults.length ? "Search Results" : "Popular in the UK", "", stations.length ? grid : empty("No stations are available.")));
    }

    async function searchRadio(query) {
        const term = query.trim();
        if (!term) {
            state.radioResults = [];
            renderRadio();
            return;
        }
        content.append(loading("Searching live radio…"));
        try {
            state.radioResults = await request(`/api/music/radio/stations?q=${encodeURIComponent(term)}&limit=40`);
            renderRadio();
        } catch (error) {
            showAlert(error.message);
        }
    }

    function toggleRadioFavourite(station) {
        if (state.radioFavourites.has(station.id)) state.radioFavourites.delete(station.id);
        else state.radioFavourites.add(station.id);
        localStorage.setItem("homeMusicRadioFavourites", JSON.stringify([...state.radioFavourites]));
        renderRadio();
        updatePlayer();
    }

    async function openArtist(id) {
        if (!id) return;
        openOverlay(loading("Loading artist…"));
        try {
            const artist = await request(`/api/music/artists/${encodeURIComponent(id)}`);
            const detail = element("div", "hm-detail");
            detail.append(closeOverlayButton());
            const hero = element("div", "hm-detail-hero");
            hero.append(image(artist.thumbnail, "hm-detail-art", artist.name, "artist"));
            const copy = element("div", "hm-detail-copy");
            copy.append(element("p", "", "ARTIST"), element("h1", "", artist.name));
            if (artist.monthly_listeners) copy.append(element("p", "", `${artist.monthly_listeners} monthly listeners`));
            const actions = element("div", "hm-detail-actions");
            if (artist.essentials?.length) {
                actions.append(button("hm-button primary", "Play", () => playTrack(artist.essentials[0], artist.essentials, 0)));
                actions.append(button("hm-button", "Shuffle", () => {
                    const shuffled = [...artist.essentials].sort(() => Math.random() - .5);
                    playTrack(shuffled[0], shuffled, 0);
                }));
            }
            copy.append(actions);
            hero.append(copy);
            detail.append(hero);
            if (artist.essentials?.length) detail.append(detailSection(`${artist.name} Essentials`, trackList(artist.essentials, "")));
            if (artist.albums?.length) detail.append(detailSection("Albums", releaseRail(artist.albums)));
            if (artist.singles?.length) detail.append(detailSection("Singles & EPs", releaseRail(artist.singles)));
            if (artist.related?.length) detail.append(detailSection("Similar Artists", releaseRail(artist.related, true)));
            if (artist.description) detail.append(detailSection("About", element("p", "", artist.description)));
            overlayPanel.replaceChildren(detail);
        } catch (error) {
            overlayPanel.replaceChildren(closeOverlayButton(), empty(error.message));
        }
    }

    async function openAlbum(id) {
        if (!id) return;
        openOverlay(loading("Loading album…"));
        try {
            const album = await request(`/api/music/albums/${encodeURIComponent(id)}`);
            const detail = element("div", "hm-detail");
            detail.append(closeOverlayButton());
            const hero = element("div", "hm-detail-hero");
            hero.append(image(album.thumbnail, "hm-detail-art", album.title));
            const copy = element("div", "hm-detail-copy");
            copy.append(element("p", "", [album.type, album.year].filter(Boolean).join(" · ").toUpperCase()), element("h1", "", album.title));
            const artist = button("hm-text-button", album.artist || "Unknown Artist", () => {
                if (album.artist_id) openArtist(album.artist_id);
            });
            copy.append(artist);
            const actions = element("div", "hm-detail-actions");
            if (album.tracks?.length) {
                actions.append(button("hm-button primary", "Play", () => playTrack(album.tracks[0], album.tracks, 0)));
                actions.append(button("hm-button", "Shuffle", () => {
                    const shuffled = [...album.tracks].sort(() => Math.random() - .5);
                    playTrack(shuffled[0], shuffled, 0);
                }));
            }
            const isSaved = state.albums.some(item => item.id === album.id);
            actions.append(button("hm-button", isSaved ? "Remove from Library" : "Add to Library", () => toggleSavedAlbum(album)));
            copy.append(actions);
            hero.append(copy);
            detail.append(hero, detailSection("Track List", trackList(album.tracks || [], "No tracks are available.")));
            overlayPanel.replaceChildren(detail);
        } catch (error) {
            overlayPanel.replaceChildren(closeOverlayButton(), empty(error.message));
        }
    }

    function detailSection(title, body) {
        const wrapper = element("section", "hm-detail-section");
        wrapper.append(element("h2", "", title), body);
        return wrapper;
    }

    async function openPlaylist(id) {
        openOverlay(loading("Loading playlist…"));
        try {
            const playlist = await request(`/api/music/playlists/${id}`);
            state.activePlaylist = playlist;
            const detail = element("div", "hm-detail");
            detail.append(closeOverlayButton());
            const hero = element("div", "hm-detail-hero");
            hero.append(playlistArtwork(playlist));
            const copy = element("div", "hm-detail-copy");
            copy.append(element("p", "", "PLAYLIST"), element("h1", "", playlist.name), element("p", "", playlist.description || `${playlist.track_count} songs`));
            const actions = element("div", "hm-detail-actions");
            if (playlist.tracks.length) {
                actions.append(button("hm-button primary", "Play", () => playTrack(playlist.tracks[0], playlist.tracks, 0)));
                actions.append(button("hm-button", "Shuffle", () => {
                    const shuffled = [...playlist.tracks].sort(() => Math.random() - .5);
                    playTrack(shuffled[0], shuffled, 0);
                }));
            }
            actions.append(button("hm-button", "Rename", () => renamePlaylist(playlist)));
            actions.append(button("hm-button danger", "Delete", () => deletePlaylist(playlist)));
            copy.append(actions);
            hero.append(copy);
            detail.append(hero, detailSection("Songs", trackList(playlist.tracks, "This playlist is empty.", {playlist})));
            overlayPanel.replaceChildren(detail);
            if (playlist.tracks.length) loadPlaylistSuggestions(playlist, detail);
        } catch (error) {
            overlayPanel.replaceChildren(closeOverlayButton(), empty(error.message));
        }
    }

    async function loadPlaylistSuggestions(playlist, detail) {
        try {
            const suggestions = await request(`/api/music/playlists/${playlist.id}/suggestions?limit=12`);
            if (!suggestions.length || state.activePlaylist?.id !== playlist.id) return;
            detail.append(detailSection("Suggested Songs", trackList(suggestions, "", {suggestionFor: playlist})));
        } catch (error) {
            showAlert(error.message);
        }
    }

    function openOverlay(node) {
        overlayPanel.replaceChildren(node);
        overlay.classList.remove("hidden");
        document.body.style.overflow = "hidden";
    }

    function closeOverlay() {
        overlay.classList.add("hidden");
        document.body.style.overflow = "";
        state.activePlaylist = null;
    }

    function closeOverlayButton() {
        const close = button("hm-overlay-close", "×", closeOverlay);
        close.setAttribute("aria-label", "Close");
        return close;
    }

    function openMenu(title, actions) {
        menuPanel.replaceChildren(element("div", "hm-menu-title", title));
        actions.forEach(action => {
            if (action.separator) {
                menuPanel.append(element("div", "hm-menu-separator"));
                return;
            }
            const item = button(`hm-menu-action${action.danger ? " danger" : ""}`, action.label, async () => {
                closeMenu();
                await action.run();
            });
            menuPanel.append(item);
        });
        menu.classList.remove("hidden");
    }

    function closeMenu() {
        menu.classList.add("hidden");
    }

    function promptForValue(title, initialValue = "") {
        return new Promise(resolve => {
            menuPanel.replaceChildren(element("div", "hm-menu-title", title));
            const input = element("input", "hm-dialog-input");
            input.value = initialValue;
            input.maxLength = 120;
            const actions = element("div", "hm-dialog-actions");
            actions.append(
                button("hm-button", "Cancel", () => {
                    closeMenu();
                    resolve(null);
                }),
                button("hm-button primary", "Save", () => {
                    const value = input.value.trim();
                    if (!value) {
                        input.focus();
                        return;
                    }
                    closeMenu();
                    resolve(value);
                })
            );
            menuPanel.append(input, actions);
            menu.classList.remove("hidden");
            window.setTimeout(() => {
                input.focus();
                input.select();
            });
            input.addEventListener("keydown", event => {
                if (event.key === "Enter") actions.lastElementChild.click();
            });
        });
    }

    function confirmAction(title, message, confirmLabel) {
        return new Promise(resolve => {
            menuPanel.replaceChildren(
                element("div", "hm-menu-title", title),
                element("p", "hm-dialog-copy", message)
            );
            const actions = element("div", "hm-dialog-actions");
            actions.append(
                button("hm-button", "Cancel", () => {
                    closeMenu();
                    resolve(false);
                }),
                button("hm-button danger", confirmLabel, () => {
                    closeMenu();
                    resolve(true);
                })
            );
            menuPanel.append(actions);
            menu.classList.remove("hidden");
        });
    }

    function openTrackMenu(track, options = {}) {
        const actions = [
            {label: "Play Next", run: () => { state.queue.unshift(track); updatePlayer(); }},
            {label: "Play Last", run: () => { state.queue.push(track); updatePlayer(); }},
            {label: track.liked ? "Remove Love" : "Love", run: () => toggleTrackLike(track)},
            {label: "Add to Playlist", run: () => choosePlaylist(track)},
            {separator: true}
        ];
        if (track.artist_id) actions.push({label: "Go to Artist", run: () => openArtist(track.artist_id)});
        if (options.playlist) {
            actions.push({label: "Remove from Playlist", danger: true, run: () => removeTrackFromPlaylist(track, options.playlist)});
        }
        if (options.suggestionFor) {
            actions.push({label: `Add to ${options.suggestionFor.name}`, run: () => addTrackToPlaylist(track, options.suggestionFor)});
        }
        openMenu(track.title, actions);
    }

    function choosePlaylist(track) {
        if (!state.playlists.length) {
            openMenu("Add to Playlist", [{label: "Create New Playlist", run: async () => {
                const playlist = await createPlaylist();
                if (playlist) await addTrackToPlaylist(track, playlist);
            }}]);
            return;
        }
        openMenu("Add to Playlist", [
            ...state.playlists.map(playlist => ({label: playlist.name, run: () => addTrackToPlaylist(track, playlist)})),
            {separator: true},
            {label: "Create New Playlist", run: async () => {
                const playlist = await createPlaylist();
                if (playlist) await addTrackToPlaylist(track, playlist);
            }}
        ]);
    }

    async function createPlaylist() {
        const name = await promptForValue("New Playlist");
        if (!name) return null;
        try {
            const playlist = await request("/api/music/playlists", {method: "POST", body: JSON.stringify({name, description: ""})});
            await loadLibrary();
            renderCurrentView();
            return playlist;
        } catch (error) {
            showAlert(error.message);
            return null;
        }
    }

    async function renamePlaylist(playlist) {
        const name = await promptForValue("Rename Playlist", playlist.name);
        if (!name || name === playlist.name) return;
        try {
            await request(`/api/music/playlists/${playlist.id}`, {method: "PATCH", body: JSON.stringify({name})});
            await loadLibrary();
            closeOverlay();
            setView("playlists");
        } catch (error) {
            showAlert(error.message);
        }
    }

    async function deletePlaylist(playlist) {
        const confirmed = await confirmAction("Delete Playlist", `Delete “${playlist.name}”? This cannot be undone.`, "Delete");
        if (!confirmed) return;
        try {
            await request(`/api/music/playlists/${playlist.id}`, {method: "DELETE", body: JSON.stringify({})});
            await loadLibrary();
            closeOverlay();
            setView("playlists");
        } catch (error) {
            showAlert(error.message);
        }
    }

    async function addTrackToPlaylist(track, playlist) {
        try {
            const updated = await request(`/api/music/playlists/${playlist.id}/tracks`, {method: "POST", body: JSON.stringify(track)});
            state.playlists = state.playlists.map(item => item.id === updated.id ? updated : item);
            showAlert(`Added “${track.title}” to ${playlist.name}.`);
            if (state.activePlaylist?.id === playlist.id) openPlaylist(playlist.id);
        } catch (error) {
            showAlert(error.message);
        }
    }

    async function removeTrackFromPlaylist(track, playlist) {
        try {
            await request(`/api/music/playlists/${playlist.id}/tracks/${encodeURIComponent(track.id)}`, {method: "DELETE", body: JSON.stringify({})});
            await loadLibrary();
            openPlaylist(playlist.id);
        } catch (error) {
            showAlert(error.message);
        }
    }

    async function toggleTrackLike(track) {
        try {
            const updated = await request(`/api/music/library/${encodeURIComponent(track.id)}`, {
                method: "PUT",
                body: JSON.stringify({...track, liked: !(track.liked === true)})
            });
            Object.assign(track, updated);
            if (state.current?.id === updated.id) Object.assign(state.current, updated);
            await loadHistoryAndLoved();
            updatePlayer();
            renderCurrentView();
        } catch (error) {
            showAlert(error.message);
        }
    }

    async function toggleSavedAlbum(album) {
        const saved = state.albums.some(item => item.id === album.id);
        try {
            if (saved) {
                await request(`/api/music/albums/library/${encodeURIComponent(album.id)}`, {method: "DELETE", body: JSON.stringify({})});
            } else {
                await request(`/api/music/albums/library/${encodeURIComponent(album.id)}`, {method: "PUT", body: JSON.stringify(album)});
            }
            await loadLibrary();
            openAlbum(album.id);
        } catch (error) {
            showAlert(error.message);
        }
    }

    function effectiveDuration() {
        if (state.currentRadio) return 0;
        if (state.playbackDuration > 0) return state.playbackDuration;
        return Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
    }

    function formatTime(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
        const minutes = Math.floor(seconds / 60);
        return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    }

    async function playTrack(track, context = [], index = 0) {
        if (!track?.id) return;
        showAlert("");
        state.transitionPending = true;
        try {
            if (state.current && state.current.id !== track.id && !state.currentRadio) state.previous.push(state.current);
            state.current = {...track};
            state.currentRadio = null;
            state.queue = context.slice(Math.max(index + 1, 0));
            state.playbackDuration = Number(track.duration_seconds) || 0;
            state.historyRecorded = false;
            updatePlayer();
            const playback = await request(`/api/music/playback-url?id=${encodeURIComponent(track.id)}`);
            state.playbackDuration = Number(playback.duration_seconds) || state.playbackDuration;
            audio.src = playback.path;
            audio.load();
            await audio.play();
            updatePlayer();
        } catch (error) {
            if (error.name !== "NotAllowedError") showAlert(error.message);
        } finally {
            state.transitionPending = false;
            updatePlayer();
        }
    }

    async function playRadio(station) {
        showAlert("");
        state.transitionPending = true;
        try {
            state.currentRadio = station;
            state.current = {id: station.id, title: station.name, artist: [station.tags?.[0], station.country].filter(Boolean).join(" · ") || "Live Radio", thumbnail: station.artwork};
            state.queue = [];
            state.playbackDuration = 0;
            audio.src = station.stream_url;
            audio.load();
            updatePlayer();
            await audio.play();
        } catch (error) {
            if (error.name === "NotAllowedError") showAlert("Press Play to start this live station.");
            else showAlert("This radio station could not be played in the browser. Try another station.");
        } finally {
            state.transitionPending = false;
            updatePlayer();
        }
    }

    async function playNext() {
        if (state.transitionPending || state.currentRadio) return;
        if (!state.queue.length && state.current) {
            try {
                const suggestions = await request("/api/music/recommendations/context", {
                    method: "POST",
                    body: JSON.stringify({
                        seed_ids: [...state.previous.slice(-2).map(item => item.id), state.current.id],
                        exclude_ids: [...state.previous, state.current].map(item => item.id),
                        limit: 12
                    })
                });
                state.queue.push(...suggestions);
            } catch (error) {
                showAlert(error.message);
            }
        }
        const next = state.queue.shift();
        if (next) await playTrack(next, [next, ...state.queue], 0);
        else {
            audio.pause();
            audio.currentTime = 0;
            updatePlayer();
        }
    }

    function playPrevious() {
        if (state.currentRadio) return;
        if (audio.currentTime > 4) {
            audio.currentTime = 0;
            return;
        }
        const previous = state.previous.pop();
        if (previous) playTrack(previous, [previous, state.current, ...state.queue].filter(Boolean), 0);
        else audio.currentTime = 0;
    }

    async function togglePlayback() {
        if (!state.current) return;
        try {
            if (audio.paused) await audio.play();
            else audio.pause();
        } catch (error) {
            if (error.name !== "NotAllowedError") showAlert(error.message);
        }
        updatePlayer();
    }

    async function recordHistory(completed = false) {
        if (!state.current || state.currentRadio) return;
        try {
            await request("/api/music/history", {
                method: "POST",
                body: JSON.stringify({...state.current, played_seconds: Math.floor(Math.min(audio.currentTime || 0, effectiveDuration() || audio.currentTime || 0)), completed})
            });
            state.historyRecorded = true;
            await loadHistoryAndLoved();
        } catch (error) {
            console.warn("HomeMusic history update failed", error);
        }
    }

    function updatePlayer() {
        const track = state.current;
        const playing = !audio.paused && !audio.ended;
        document.getElementById("hm-mini-player").classList.toggle("hidden", !track);
        root.classList.toggle("has-player", Boolean(track));
        document.getElementById("hm-mini-title").textContent = track?.title || "Nothing Playing";
        document.getElementById("hm-mini-artist").textContent = track?.artist || "Choose something to play";
        const miniImage = document.getElementById("hm-mini-image");
        const miniPlaceholder = document.getElementById("hm-mini-placeholder");
        if (track?.thumbnail) {
            miniImage.src = track.thumbnail;
            miniImage.onerror = () => {
                miniImage.onerror = null;
                miniImage.src = fallbackArtwork(track.title, state.currentRadio ? "radio" : "music");
            };
            miniImage.classList.remove("hidden");
            miniPlaceholder.classList.add("hidden");
        } else {
            miniImage.classList.add("hidden");
            miniPlaceholder.classList.remove("hidden");
        }
        document.getElementById("hm-play").setAttribute("aria-label", playing ? "Pause" : "Play");
        document.getElementById("hm-play-icon").setAttribute("d", playing ? "M7 5h4v14H7V5Zm6 0h4v14h-4V5Z" : "m8 5 11 7-11 7V5Z");
        const love = document.getElementById("hm-love");
        love.classList.toggle("active", state.currentRadio ? state.radioFavourites.has(state.currentRadio.id) : track?.liked === true);
        love.setAttribute("aria-label", state.currentRadio ? "Favourite station" : "Love");
        updateProgress();
        updateMediaSession();
        if (!overlay.classList.contains("hidden") && overlayPanel.querySelector(".hm-now-playing")) renderNowPlaying();
    }

    function updateProgress() {
        const duration = effectiveDuration();
        const current = duration ? Math.min(audio.currentTime || 0, duration) : audio.currentTime || 0;
        document.getElementById("hm-progress").value = duration ? String(Math.min(1000, (current / duration) * 1000)) : "0";
        document.getElementById("hm-current-time").textContent = state.currentRadio ? "LIVE" : formatTime(current);
        document.getElementById("hm-duration").textContent = state.currentRadio ? "" : formatTime(duration);
        if ("mediaSession" in navigator && duration > 0 && current <= duration) {
            try {
                navigator.mediaSession.setPositionState({duration, playbackRate: audio.playbackRate, position: Math.max(0, current)});
            } catch (_) {
                return;
            }
        }
    }

    function updateMediaSession() {
        if (!("mediaSession" in navigator) || !state.current) return;
        const artwork = state.current.thumbnail ? [{src: state.current.thumbnail, sizes: "512x512"}] : [];
        navigator.mediaSession.metadata = new MediaMetadata({
            title: state.current.title,
            artist: state.current.artist,
            album: state.currentRadio ? "Live Radio" : "HomeMusic",
            artwork
        });
        navigator.mediaSession.playbackState = audio.paused ? "paused" : "playing";
    }

    function renderNowPlaying() {
        const track = state.current;
        if (!track) return;
        const wrapper = element("div", "hm-now-playing");
        const main = element("div", "hm-now-main");
        main.append(closeOverlayButton(), image(track.thumbnail, "hm-now-art", track.title));
        const copy = element("div", "hm-now-copy");
        copy.append(element("h1", "", track.title));
        const artist = button("", track.artist, () => {
            if (track.artist_id) openArtist(track.artist_id);
        });
        copy.append(artist);
        main.append(copy);
        if (!state.currentRadio) {
            const progress = element("div", "hm-now-progress");
            const slider = element("input");
            slider.type = "range";
            slider.min = "0";
            slider.max = "1000";
            slider.value = effectiveDuration() ? String(Math.min(1000, (audio.currentTime / effectiveDuration()) * 1000)) : "0";
            slider.setAttribute("aria-label", "Playback position");
            slider.addEventListener("input", () => {
                const duration = effectiveDuration();
                if (duration) audio.currentTime = (Number(slider.value) / 1000) * duration;
            });
            progress.append(slider, element("span", "", formatTime(audio.currentTime)), element("span", "", `−${formatTime(Math.max(effectiveDuration() - audio.currentTime, 0))}`));
            main.append(progress);
        } else {
            main.append(element("p", "hm-live", "LIVE RADIO"));
        }
        const controls = element("div", "hm-now-controls");
        if (!state.currentRadio) controls.append(iconButton('<svg viewBox="0 0 24 24"><path d="M6 5v14M19 6l-9 6 9 6V6Z"/></svg>', "Previous", playPrevious));
        const play = button("hm-now-play", "", togglePlayback);
        play.setAttribute("aria-label", audio.paused ? "Play" : "Pause");
        play.innerHTML = audio.paused ? icons.play : icons.pause;
        controls.append(play);
        if (!state.currentRadio) controls.append(iconButton('<svg viewBox="0 0 24 24"><path d="M18 5v14M5 6l9 6-9 6V6Z"/></svg>', "Next", playNext));
        main.append(controls);
        const actions = element("div", "hm-detail-actions");
        actions.append(button("hm-button", state.currentRadio ? (state.radioFavourites.has(state.currentRadio.id) ? "Remove Favourite" : "Favourite") : (track.liked ? "Remove Love" : "Love"), () => {
            if (state.currentRadio) toggleRadioFavourite(state.currentRadio);
            else toggleTrackLike(track);
        }));
        if (!state.currentRadio) {
            actions.append(button("hm-button", "Add to Playlist", () => choosePlaylist(track)));
        }
        main.append(actions);
        wrapper.append(main);
        const queue = element("aside", "hm-now-queue");
        queue.append(element("h2", "", "Up Next"));
        if (!state.queue.length) queue.append(empty("HomeMusic will choose something when this song ends."));
        state.queue.forEach((item, index) => {
            const row = element("div", "hm-queue-row");
            const copyNode = element("div");
            copyNode.append(element("strong", "", item.title), element("span", "", item.artist));
            const buttons = element("div", "hm-queue-buttons");
            buttons.append(
                button("", "↑", () => moveQueueItem(index, -1)),
                button("", "↓", () => moveQueueItem(index, 1)),
                button("", "×", () => removeQueueItem(index))
            );
            row.append(image(item.thumbnail, "", item.title), copyNode, buttons);
            queue.append(row);
        });
        wrapper.append(queue);
        overlayPanel.replaceChildren(wrapper);
    }

    function moveQueueItem(index, offset) {
        const destination = index + offset;
        if (destination < 0 || destination >= state.queue.length) return;
        const [item] = state.queue.splice(index, 1);
        state.queue.splice(destination, 0, item);
        renderNowPlaying();
    }

    function removeQueueItem(index) {
        state.queue.splice(index, 1);
        renderNowPlaying();
    }

    async function loadHome() {
        try {
            state.home = await request("/api/music/home");
        } catch (error) {
            showAlert(error.message);
        }
    }

    async function loadHistoryAndLoved() {
        const [history, loved] = await Promise.all([
            request("/api/music/history"),
            request("/api/music/library")
        ]);
        state.history = history;
        state.loved = loved;
    }

    async function loadLibrary() {
        const [albums, playlists] = await Promise.all([
            request("/api/music/albums/library"),
            request("/api/music/playlists")
        ]);
        state.albums = albums;
        state.playlists = playlists;
    }

    async function loadRadio() {
        state.radio = await request("/api/music/radio/stations?limit=40");
    }

    async function initialise() {
        content.replaceChildren(loading("Personalising HomeMusic…"));
        const results = await Promise.allSettled([loadHome(), loadHistoryAndLoved(), loadLibrary(), loadRadio()]);
        const failure = results.find(result => result.status === "rejected");
        if (failure) showAlert(failure.reason?.message || "Some HomeMusic content could not be loaded.");
        renderCurrentView();
        updatePlayer();
    }

    document.querySelectorAll("[data-view]").forEach(item => item.addEventListener("click", () => setView(item.dataset.view)));
    document.getElementById("music-search-form").addEventListener("submit", event => {
        event.preventDefault();
        historyPanel.classList.add("hidden");
        searchMusic(searchInput.value);
    });
    searchInput.addEventListener("focus", renderSearchHistory);
    searchInput.addEventListener("input", () => {
        if (!searchInput.value) renderSearchHistory();
    });
    document.addEventListener("click", event => {
        if (!event.target.closest(".hm-search")) historyPanel.classList.add("hidden");
    });
    document.querySelectorAll("[data-close-overlay]").forEach(item => item.addEventListener("click", closeOverlay));
    document.querySelectorAll("[data-close-menu]").forEach(item => item.addEventListener("click", closeMenu));
    document.getElementById("hm-player-summary").addEventListener("click", () => {
        if (state.current) {
            openOverlay(element("div"));
            renderNowPlaying();
        }
    });
    document.getElementById("hm-play").addEventListener("click", togglePlayback);
    document.getElementById("hm-previous").addEventListener("click", playPrevious);
    document.getElementById("hm-next").addEventListener("click", playNext);
    document.getElementById("hm-love").addEventListener("click", () => {
        if (state.currentRadio) toggleRadioFavourite(state.currentRadio);
        else if (state.current) toggleTrackLike(state.current);
    });
    document.getElementById("hm-queue-toggle").addEventListener("click", () => {
        if (state.current) {
            openOverlay(element("div"));
            renderNowPlaying();
        }
    });
    document.getElementById("hm-progress").addEventListener("input", event => {
        const duration = effectiveDuration();
        if (duration) audio.currentTime = (Number(event.target.value) / 1000) * duration;
    });

    audio.addEventListener("play", () => {
        showAlert("");
        updatePlayer();
    });
    audio.addEventListener("pause", updatePlayer);
    audio.addEventListener("loadedmetadata", updateProgress);
    audio.addEventListener("durationchange", updateProgress);
    audio.addEventListener("ended", async () => {
        if (state.transitionPending || state.currentRadio) return;
        state.transitionPending = true;
        await recordHistory(true);
        state.transitionPending = false;
        await playNext();
    });
    audio.addEventListener("timeupdate", () => {
        updateProgress();
        const duration = effectiveDuration();
        if (!state.currentRadio && audio.currentTime >= 30 && !state.historyRecorded) recordHistory(false);
        if (!state.currentRadio && duration > 0 && audio.currentTime >= duration - .25 && !audio.paused && !state.transitionPending) {
            state.transitionPending = true;
            recordHistory(true).finally(() => {
                state.transitionPending = false;
                playNext();
            });
        }
    });
    audio.addEventListener("error", () => {
        if (!state.transitionPending) showAlert(state.currentRadio ? "This live station stopped responding." : "This song could not be played. Try another result.");
        updatePlayer();
    });

    if ("mediaSession" in navigator) {
        navigator.mediaSession.setActionHandler("play", () => audio.play());
        navigator.mediaSession.setActionHandler("pause", () => audio.pause());
        navigator.mediaSession.setActionHandler("previoustrack", playPrevious);
        navigator.mediaSession.setActionHandler("nexttrack", playNext);
        navigator.mediaSession.setActionHandler("seekto", details => {
            const duration = effectiveDuration();
            if (duration && Number.isFinite(details.seekTime)) audio.currentTime = Math.min(details.seekTime, duration);
        });
    }

    document.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            closeMenu();
            closeOverlay();
        }
        if (event.code === "Space" && !event.target.closest("input,button,textarea")) {
            event.preventDefault();
            togglePlayback();
        }
    });

    initialise();
})();
