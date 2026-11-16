# Glossary

The terms used in Usher's code, comments, docs and issues. Use these rather than informal or installation-specific words.

| Term | Means |
| --- | --- |
| **Server** | The Usher backend (`usher/`): the Python process that talks to the integrations and serves the web client. |
| **Web client** / **PWA** | The Home Screen app (`web/`) people open on their phone, tablet or computer. |
| **Integrations** | The services Usher talks to: the PVRs, the indexer manager, the download client and the media server. |
| **PVRs** | Sonarr (TV shows) and Radarr (movies), which decide what to fetch and file it in the library. |
| **Indexer manager** | Prowlarr, which supplies the PVRs with indexers to search. |
| **Download client** | Transmission, which downloads what the PVRs pick. |
| **Media server** | Jellyfin, where titles are watched; its watch history drives Discover and Tidy up. |
| **Viewing account** | The media server account whose watch history Usher reads (`media_server.user_id`). |
| **Library folder** | A root folder in a PVR where titles are filed (`library.tv_folder`, `library.movie_folder`). |
| **Family-friendly** | A title with a rating in `family.ratings` (G, PG, TV-Y, TV-Y7, TV-Y7-FV, TV-G or TV-PG by default). With `family.tv_folder` / `family.movie_folder` set, these are filed in separate family-friendly library folders. |
| **Operator** | Whoever installs and runs Usher: sets up the server, `usher.toml` and accounts. |
| **User** | Anyone with an Usher account who signs in to the web client. |
| **Site settings** | Everything specific to one installation, kept in `usher.toml` and `.env` on the server, never in this repo. |
