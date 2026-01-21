# VinylCat (Docker / Portainer)

Self-hosted vinyl catalog with:
- Accounts (email + password)
- Collections per user
- Share collections with other users (viewer/editor)
- Discogs metadata lookup (barcode/title/artist/year)
- Optional front/back cover upload on Add page:
  - OCR service extracts barcode + candidate artist/title/year
  - Autofills the search form
- Dark mode + multiple themes (Bootswatch theme switcher)

## Deploy with Portainer
1. Create a new Stack and paste `docker-compose.yml`
2. Set environment variable `DISCOGS_TOKEN` (recommended)
3. Deploy

### Ports
- App: http://HOST:8080
- Adminer (optional DB UI): http://HOST:8081
- OCR service: http://HOST:8090 (internal use; exposed for debugging)

## First steps
1. Open the app, register an account.
2. Go to **Collections** to create and share collections.
3. Add records:
   - optionally upload front/back images and click **Analyze images**
   - then search Discogs and pick the right release.

## Notes
- Sharing: the invited user must register first with their email.
- Roles:
  - viewer: can browse only
  - editor: can add/delete and upload photos
  - owner: can share and manage collaborators

## Security
- Change `SECRET_KEY` in compose before exposing to the internet.
- Put behind a reverse proxy (Traefik/Nginx) + HTTPS if remote access is needed.


## Account management
Go to **Account** in the top navigation to:
- Export your owned collections/records as JSON
- Import a previous VinylCat export (creates new collections under your account)
- Delete your account (removes owned data and unshares shared collections)

Note: exports do not embed uploaded image files; Discogs image URLs are preserved.
