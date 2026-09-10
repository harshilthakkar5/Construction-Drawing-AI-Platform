# Running the platform on one laptop, in Docker

Everything — frontend, API, worker, Postgres, Redis, Qdrant and object storage
— in containers on a single laptop, opened at `http://localhost`. One person
uses it; nothing needs to be reachable from anywhere else.

This is the **same deployment** as the office server in
`docs/local-server-deployment.md`, sized down and reached at `localhost`
instead of a LAN address. Same compose file, same images, same storage
backend. The differences are four values in one env file, so read this guide
rather than that one, but know they are one thing.

> **Read this first: it is not offline.** `STORAGE_BACKEND=local` decides where
> the *files* live. Chat, summaries and embeddings still call Anthropic and
> Google over the internet, so the laptop needs a working connection and an API
> key, and the text extracted from the drawings leaves the machine when a
> question is asked. Nothing here makes the system air-gapped. Upload,
> extraction, OCR and the PDF viewer do work with no key at all — chunks simply
> wait unembedded and the chat returns 503 until you add one.

---

## 1. What the laptop needs

| | Minimum | Comfortable |
|---|---|---|
| RAM | 16 GB | 32 GB |
| CPU | 4 cores | 8+ cores |
| Free disk | 60 GB | 200 GB+ |
| OS | Windows 10/11 Pro, macOS 12+, or Linux | — |

8 GB will not work. Postgres, Redis, Qdrant, MinIO, the API and a PyMuPDF
worker holding full-resolution page images do not fit, and on Windows they all
share one VM whose memory is capped below the machine's.

**Disk is the one people underestimate.** Three things consume it:

- **The images**, several GB — PaddleOCR and its dependencies dominate. Check
  after the first build with `docker system df`.
- **The drawings**, roughly the source PDFs *again*: each page is stored as a
  rendered PNG plus a thumbnail plus its text. For line drawings that is
  perhaps 1–3 MB per page, so a 1000-page set lands somewhere around 1–3 GB on
  top of the PDFs. Estimated, not measured — watch the first real project.
- **Postgres and Qdrant**, small by comparison: chunk text and vectors.

## 2. Install Docker

**Windows.** Docker Desktop needs WSL2. In an admin PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot, let Ubuntu finish its first-run setup (it asks for a username and
password), then install Docker Desktop from docker.com and enable
**Settings → Resources → WSL Integration → Ubuntu**.

**macOS.** Install Docker Desktop. On Apple Silicon everything here has an
arm64 image, so there is nothing to configure.

**Linux.** Install Docker Engine and the compose plugin from your distribution,
then `sudo usermod -aG docker $USER` and log out and back in.

Check it:

```bash
docker --version && docker compose version
```

## 3. Give the VM enough memory

On **Windows and macOS**, containers run inside a Linux VM whose memory cap is
what actually binds — not the laptop's RAM.

The laptop preset in §5 sets container ceilings summing to **7.75 GB**, so the
VM needs more than that, with headroom.

**Windows** — create `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=10GB
processors=6
swap=4GB
```

Then `wsl --shutdown` in PowerShell and reopen Docker Desktop.

**macOS** — Docker Desktop → Settings → Resources → Memory: **10 GB**.

**Linux** — nothing to do; containers use the host directly.

## 4. Get the code

On **Windows, work inside WSL**, not on the Windows drive. A repo under
`/mnt/c` is served through a filesystem bridge that costs several times the
throughput on exactly the large files this handles. Open the **Ubuntu**
terminal:

```bash
sudo apt update && sudo apt install -y git
git clone <your repo url> ~/cdip
cd ~/cdip
git checkout claude/voyga-replacement-options-cyalps
```

Windows Explorer can reach it at `\\wsl$\Ubuntu\home\<you>\cdip` when you need
to drag a PDF in.

On **macOS/Linux**, clone anywhere you like.

## 5. Configure

```bash
cp deploy/.env.local.example deploy/.env.local
chmod 600 deploy/.env.local
nano deploy/.env.local        # or any editor
```

Four edits. The file explains each one where it sits.

**a. Point storage at localhost.** The browser is on this same machine, so
this is the whole change:

```bash
LOCAL_S3_PUBLIC_ENDPOINT=http://localhost:9000
APP_URL=http://localhost
```

This value exists because uploads never pass through the API — the browser gets
a presigned URL and PUTs the bytes straight at object storage, which is what
lets a 1 GB PDF upload resumably. A presigned URL is signed against the host it
will be *requested* on, so it must be the address the **browser** uses. On a
laptop that is `localhost`; on the office server it is a LAN IP. Get it wrong
and every upload is rejected as `SignatureDoesNotMatch`.

**b. Two passwords.** Any long random strings — you will not type them again:

```bash
openssl rand -base64 24    # run twice
```

```bash
POSTGRES_PASSWORD=<first>
LOCAL_S3_SECRET=<second>
```

**c. Switch to the laptop preset.** Comment out the six `*_MEM_LIMIT` lines and
the three `*_CONCURRENCY` lines under "size", and uncomment the LAPTOP PRESET
block below them:

```bash
PROCESS_CONCURRENCY=1
PAGE_CONCURRENCY=2
SCRAPE_CONCURRENCY=2
WORKER_MEM_LIMIT=4g
API_MEM_LIMIT=768m
POSTGRES_MEM_LIMIT=1g
REDIS_MEM_LIMIT=512m
QDRANT_MEM_LIMIT=1g
MINIO_MEM_LIMIT=512m
```

`PROCESS_CONCURRENCY × PAGE_CONCURRENCY` is pages being rendered at once, each
holding a full-resolution pixmap — around 54 MB for a D-size sheet at
`PAGE_RENDER_ZOOM=2`, before the PNG encode buffer. Two in flight instead of
sixteen makes ingest slower and nothing else different.

**d. Your API keys.**

```bash
ANTHROPIC_API_KEY=sk-ant-...
EMBEDDING_PROVIDER=gemini
GEMINI_API_KEY=...
```

Leave `STORAGE_BACKEND=local` alone — that is the whole point of this install.

## 6. Deploy

```bash
./deploy/deploy.sh local
```

It builds the images, runs the database migrations, starts the eight
containers, and waits for `/health`. **The first build takes 10–20 minutes**,
mostly PaddleOCR's dependencies. Later runs are cached and take about a minute.

Then open **`http://localhost`**.

Confirm the storage switch took effect:

```bash
curl -s http://localhost/api/health
# {"status":"ok","checks":{"postgres":"ok","redis":"ok","qdrant":"ok"},
#  "storage":{"backend":"local","bucket":"cdip-local"}}
```

If port 80 is already taken on this laptop, set `WEB_PORT=8080` in
`deploy/.env.local` (and `APP_URL=http://localhost:8080`) and re-run. Only the
published port changes; nothing inside the containers needs editing.

## 7. First run

1. **Create an account** at `http://localhost` — the first registration is just
   a normal signup, there is no separate admin step.
2. **Create a project**, give it a name and one or more roles.
3. **Upload PDFs.** They go straight from the browser to object storage;
   the upload is resumable, so a dropped connection resumes rather than
   restarting.
4. **Mark the title-block region** — drag one box over the sheet number on a
   single page. That box is applied to every page of every PDF to read the
   sheet numbers and sort pages into disciplines.
5. **Generate summaries** per discipline, from the button on each card. Nothing
   is summarized automatically, because it costs money.
6. **Chat.**

Watch the first ingest while it runs:

```bash
docker compose -f deploy/docker-compose.local.yml logs -f worker
```

Every stage is logged (`1/6 download` … `6/6 finalize`).

## 8. Every day

```bash
cd ~/cdip

# start (also after a reboot — containers restart themselves once Docker is up)
docker compose -f deploy/docker-compose.local.yml up -d

# stop, keeping all data
docker compose -f deploy/docker-compose.local.yml stop

# what is running, and what it is using
docker compose -f deploy/docker-compose.local.yml ps
docker stats

# update to newer code (pull + rebuild + migrate + restart)
./deploy/deploy.sh local
```

`restart: unless-stopped` means the stack comes back by itself after a reboot,
as soon as Docker Desktop starts. On Windows, tick **Settings → General →
Start Docker Desktop when you sign in** so that actually happens.

The MinIO console — for browsing the stored files by hand — is at
`http://localhost:9001`, username `LOCAL_S3_KEY`, password `LOCAL_S3_SECRET`.

## 9. Backups, and moving to another laptop

Two things matter, and the drawings are the smaller one:

```bash
mkdir -p ~/backups

# 1. Postgres — projects, pages, chunks, portions, summaries, chat history.
docker compose -f deploy/docker-compose.local.yml exec -T postgres \
  pg_dump -U cdip cdip | gzip > ~/backups/cdip-$(date +%F).sql.gz

# 2. The drawings — an ordinary folder (STORAGE_DATA_DIR, default ./data/storage).
tar czf ~/backups/storage-$(date +%F).tar.gz -C ~/cdip/data storage
```

Postgres is the one that matters. PDFs can be re-uploaded and vectors
recomputed from `POST /projects/:id/documents/reindex`; the portion structure,
the approved summaries and the chat history cannot.

**To move to another laptop:** install Docker there, clone the repo, copy
`deploy/.env.local` and both backups over, run `./deploy/deploy.sh local`,
then restore:

```bash
gunzip -c cdip-2026-09-09.sql.gz | \
  docker compose -f deploy/docker-compose.local.yml exec -T postgres psql -U cdip cdip
tar xzf storage-2026-09-09.tar.gz -C ~/cdip/data
```

Qdrant is not in the backup on purpose — everything in it is derived. Rebuild
it with a reindex.

## 10. When something goes wrong

**Uploads fail; the browser console shows `SignatureDoesNotMatch`.**
`LOCAL_S3_PUBLIC_ENDPOINT` does not match the address the browser actually
used. It must be `http://localhost:9000` for a laptop install. If you changed
`MINIO_PORT`, the port here has to change with it — they are one port seen
from two places.

**Uploads hang, or the browser cannot reach port 9000.** Check MinIO is up
(`docker compose -f deploy/docker-compose.local.yml ps`). Uploads go *directly*
to it, so an app that loads but cannot upload is almost always this.

**A container keeps restarting.** `docker stats` while it happens: if it dies
at its `mem_limit`, either raise that limit or lower
`PROCESS_CONCURRENCY`/`PAGE_CONCURRENCY`. If everything is starved at once, the
VM cap in §3 is too low.

**The API says `503` on chat.** No API key for the active `EMBEDDING_PROVIDER`,
or no `ANTHROPIC_API_KEY`. The response names the missing one. Add it to
`deploy/.env.local` and re-run `./deploy/deploy.sh local`.

**Chunks stay unembedded.** Same cause — the worker skips embedding without a
key and leaves `embeddingId` NULL. After adding the key, fill them in with
`POST /projects/:id/documents/reindex`.

**`no space left on device`.** `docker system df`, then
`docker image prune -a` for images from old builds. The drawings folder is
separate and is not touched by pruning.

**Ingest is very slow on Windows.** Check the repo is not under `/mnt/c`. §4.

**The whole thing is unreachable after a reboot.** Docker Desktop is not
running. Start it, and turn on start-at-login.

## 11. If this laptop belongs to a client

Three things are worth saying out loud before you hand it over.

- **The API key is in a plaintext file on their machine**, readable by anyone
  who can read `deploy/.env.local`, and every call bills to that key. If that
  is not acceptable, the key belongs on a server you control and the laptop
  belongs on the office-server or DigitalOcean shape instead.
- **Registration is open.** Anyone who can reach the app can create an account.
  On `localhost` that is only someone at the keyboard, which is fine — but it
  stops being fine the moment the same install is exposed on a network.
- **There is no TLS and no backup schedule.** Both are correct for `localhost`
  and neither survives the install being "just put on the office wifi". If it
  moves to a shared machine, follow `docs/local-server-deployment.md`, which
  covers the firewall rules and states the plain-HTTP trade-off explicitly.

## 12. Moving off the laptop later

Nothing here is a dead end. The drawings sit in an S3-compatible bucket under
the same keys DigitalOcean Spaces would use, so switching is a mirror and one
word:

```bash
mc alias set local  http://localhost:9000                "$LOCAL_S3_KEY" "$LOCAL_S3_SECRET"
mc alias set spaces https://blr1.digitaloceanspaces.com  "$SPACES_KEY"   "$SPACES_SECRET"
mc mirror --overwrite local/cdip-local spaces/your-bucket
```

then `STORAGE_BACKEND=spaces` and a restart. Because the keys are identical on
both sides, the database needs no change at all. `deploy/docker-compose.app.yml`
and `deploy/docker-compose.worker.yml` are the two-Droplet shape when it
outgrows one machine.
