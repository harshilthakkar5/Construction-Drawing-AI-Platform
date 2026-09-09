# Running the whole platform on one office PC

The target is the machine in the diagram: Windows 11 Pro, 32 GB, an i7-12700F,
an RTX 3050, a 2.75 TB drive, on the office wifi. Everything runs on it —
frontend, API, worker, Postgres, Redis, Qdrant and object storage — and the
other laptops in the office open it in a browser.

Nothing about the application changes to make this work. Object storage is the
only piece that would ordinarily be a cloud service, and MinIO speaks the same
S3 API DigitalOcean Spaces does, so `STORAGE_BACKEND=local` swaps a set of
credentials rather than selecting a second code path that could rot untested.

| | Office PC | DigitalOcean |
|---|---|---|
| Cost | electricity | ~$60–90/month |
| Reachable from | the office network | anywhere |
| Uptime | while the PC is on | managed |
| Backups | yours to run | managed daily |
| Drawings live | on this disk | in Spaces |
| Model API calls | still go to Anthropic / Google | same |

That last row matters: `local` is about where the FILES live. Chat, summaries
and embeddings still call Anthropic and Google over the internet, so the PC
needs a working connection and the drawings' text still leaves the building
when a question is asked. Nothing here makes the system offline or air-gapped.

---

## 1. Windows setup

**Turn off sleep.** A server that suspends at 22:00 is not a server.
Settings → System → Power → Screen and sleep → *When plugged in, put my device
to sleep after* → **Never**. Also uncheck fast startup, which skips the clean
shutdown Postgres wants (Control Panel → Power Options → Choose what the power
buttons do → Change settings that are currently unavailable).

**Install WSL2 and Docker Desktop.** In an admin PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot, let Ubuntu finish its first-run setup, then install Docker Desktop and
enable **Settings → Resources → WSL Integration → Ubuntu**. Also tick
**Settings → General → Start Docker Desktop when you sign in** and
**Settings → General → Use the WSL 2 based engine**.

**Give the WSL VM enough memory.** This is the ceiling everything else lives
under, and it is not the PC's 32 GB — WSL2 takes half by default and Docker
Desktop's containers all live inside it. Create `C:\Users\<you>\.wslconfig`:

```ini
[wsl2]
memory=24GB
processors=10
swap=8GB
```

Then `wsl --shutdown` in PowerShell and reopen Docker Desktop.

**Give the PC a fixed address.** On the router, add a DHCP reservation for this
PC's MAC address. Every laptop's bookmark and one value in the env file are
that address; if DHCP moves it, every one of them breaks at once.

**Open the two ports.** In an admin PowerShell:

```powershell
New-NetFirewallRule -DisplayName "CDIP web"     -Direction Inbound -LocalPort 80   -Protocol TCP -Action Allow -Profile Private
New-NetFirewallRule -DisplayName "CDIP storage" -Direction Inbound -LocalPort 9000 -Protocol TCP -Action Allow -Profile Private
```

`-Profile Private` restricts them to networks Windows classes as private; make
sure the office wifi is set to Private, not Public, in Settings → Network.

Port 9000 has to be open to the office and not only to this PC, which surprises
people. Uploads never pass through the API — the browser gets a presigned URL
and PUTs the bytes straight at object storage, which is what lets a 1 GB PDF
upload resumably without the API holding it in memory. Page images and
thumbnails are fetched the same way. Port 80 alone gets you an app that loads
and cannot upload anything.

## 2. Get the code

Work **inside** WSL, not on the Windows drive. A repo under `/mnt/c` is served
through a filesystem bridge that costs several times the throughput on exactly
the large files this handles.

```bash
# in the Ubuntu terminal
sudo apt update && sudo apt install -y git
git clone <your repo url> ~/cdip
cd ~/cdip
git checkout claude/voyga-replacement-options-cyalps
```

Windows Explorer can see it at `\\wsl$\Ubuntu\home\<you>\cdip` when you need to
drag a file in.

## 3. Configure

```bash
cp deploy/.env.local.example deploy/.env.local
chmod 600 deploy/.env.local
nano deploy/.env.local
```

Three values must be edited before the first run; the file says so at the top.

- **`LOCAL_S3_PUBLIC_ENDPOINT`** — `http://<this PC's LAN IP>:9000`. Read the
  address off **Windows** (`ipconfig`, the wifi or Ethernet adapter's IPv4
  address), not from inside WSL: WSL's own address is on a virtual network no
  other laptop can reach.
- **`POSTGRES_PASSWORD`** and **`LOCAL_S3_SECRET`** — long random strings.
  `openssl rand -base64 32` twice.

Then `APP_URL` to `http://<that same IP>` (it goes into password-reset emails),
and your `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`.

`STORAGE_DATA_DIR` is where the drawings land — an ordinary folder, default
`~/cdip/data/storage`. Keep it on the Linux side; see §7 for what to do when
the 2.75 TB Windows drive is where you want them.

## 4. Deploy

```bash
./deploy/deploy.sh local
```

It builds the images, runs the database migrations, starts everything, and
waits for `/health`. The first build takes 10–20 minutes, mostly PaddleOCR's
dependencies; later ones are cached and take about a minute.

Then, from any laptop on the office wifi: **`http://<the PC's IP>`**.

Check which storage backend actually took effect:

```bash
curl -s http://localhost/api/health
# {"status":"ok","checks":{...},"storage":{"backend":"local","bucket":"cdip-local"}}
```

## 5. Switching between local disk and Spaces

One word in `deploy/.env.local`:

```bash
STORAGE_BACKEND=local     # MinIO on this PC
STORAGE_BACKEND=spaces    # DigitalOcean Spaces, from the SPACES_* block
```

then `./deploy/deploy.sh local`. Both sets of credentials live in the file at
once, which is what makes it a switch rather than a rewrite. The API and the
worker read the same file and resolve it with the same shared rule
(`apps/api/src/storageConfig.ts`, `workers/src/storage_config.py`, tested
against one fixture), so they cannot end up on different backends — which would
not be a degraded system but a worker unable to find any file it is handed.

**The switch does not move anything.** Object keys are recorded in Postgres, and
the two stores hold different bytes: a project uploaded under one backend has
nothing under the other, so its pages stop rendering and re-processing fails to
download. Switch on an empty system, or copy the objects across first:

```bash
# install mc, then
mc alias set local  http://<pc-ip>:9000            "$LOCAL_S3_KEY" "$LOCAL_S3_SECRET"
mc alias set spaces https://blr1.digitaloceanspaces.com "$SPACES_KEY"  "$SPACES_SECRET"
mc mirror --overwrite local/cdip-local spaces/your-bucket
```

Keys are identical on both sides, so a mirrored bucket needs no database
changes. This is also the migration path in the other direction, the day the
office PC becomes the bottleneck and the two Droplets in
`deploy/docker-compose.app.yml` take over.

## 6. Day to day

```bash
cd ~/cdip
docker compose -f deploy/docker-compose.local.yml ps            # what is running
docker compose -f deploy/docker-compose.local.yml logs -f worker # follow an ingest
docker stats                                                     # memory, live
./deploy/deploy.sh local                                         # pull + rebuild + migrate
```

The MinIO console, for browsing the stored files by hand, is at
`http://localhost:9001` **on the server PC only** — it holds the root
credentials, so it is bound to loopback and is not reachable from the office.
Postgres (5432), Redis (6379) and Qdrant (6333) are bound the same way.

**Backups.** Three things, and the drawings are the smallest problem:

```bash
# 1. Postgres — every project, page, chunk, portion, summary and chat message.
docker compose -f deploy/docker-compose.local.yml exec -T postgres \
  pg_dump -U cdip cdip | gzip > ~/backups/cdip-$(date +%F).sql.gz

# 2. The drawings — an ordinary folder.
rsync -a ~/cdip/data/storage/ /mnt/d/backups/storage/

# 3. Qdrant — rebuildable from Postgres (reindex), so snapshot it or don't.
./deploy/qdrant-snapshot.sh
```

Postgres is the one that matters. The PDFs can be re-uploaded and the vectors
re-computed; the portion structure, the approved summaries and the chat history
cannot.

## 7. Putting the drawings on the big Windows drive

`STORAGE_DATA_DIR=/mnt/d/cdip-storage` works, and it is slower — every read and
write crosses the Windows filesystem bridge, which is the one thing WSL2 is bad
at and 1 GB PDFs are the worst case for. Two better options:

- Keep `STORAGE_DATA_DIR` on the Linux side and `rsync` to the big drive
  nightly. Fast in use, and the copy on D: is a backup rather than a
  single point of failure.
- Or move the whole WSL distribution onto the big drive
  (`wsl --export` / `wsl --import`), which puts everything on it at native
  speed.

Postgres is a separate case: it is a **named Docker volume**, not a bind mount,
and must stay that way. It refuses to start on a Windows-mounted path because
it cannot set the ownership and `0700` permissions it checks for on its data
directory.

## 8. Tuning the worker

`PROCESS_CONCURRENCY × PAGE_CONCURRENCY` pages are rendered at once, each
holding a full-resolution pixmap of what may be a D-size sheet at ~18
megapixels. The example file ships 4 × 4 = 16 in flight against the worker's
8 GB limit, which is comfortable on this machine. Raise them together while
watching `docker stats`, and remember the ceiling is what `.wslconfig` gave the
VM, not what the PC has.

Extraction is network-bound in the cloud deployment — every page ships a PNG, a
thumbnail and a text file to Spaces, and a measured production run managed 6.7
pages/minute against it. On this PC that write is a local disk, so the same
work should run considerably faster and the CPU becomes the limit. Measure it
rather than assuming:

```bash
# --upload times the real thing (render + thumbnail + text, all three written
# to storage). Without the flag it times CPU only and overstates throughput.
# The repo is mounted because the worker image ships src/ alone, while the
# benchmark expects the checkout's layout.
docker compose -f deploy/docker-compose.local.yml run --rm \
  -v "$PWD:/repo" -w /repo worker \
  python benchmarks/extract_throughput.py /repo/sample.pdf --upload
```

Point it at a real drawing set — a synthetic PDF measures nothing.

**The RTX 3050 is not used.** Nothing in the pipeline asks for a GPU: PyMuPDF
renders on the CPU, and PaddleOCR is installed as the CPU build
(`paddlepaddle==2.6.2` in `workers/requirements.txt`). OCR runs only on pages
that arrive with no text layer, which for CAD-exported drawings is usually few
of them — so a GPU build would speed up a step that is rarely the bottleneck,
at the cost of a CUDA image and a pinned driver. Worth doing only if the logs
show OCR dominating a real ingest.

## 9. What you are accepting

- **No TLS.** A LAN address has no certificate anyone can issue, so Caddy
  serves plain HTTP and every session cookie, chat message and presigned URL
  crosses the office wifi readable by anyone on it. Acceptable on a trusted
  office network and nowhere else. **Do not port-forward this from the
  router.** If it must be reachable from outside the office, put it behind a
  VPN — Tailscale is the least work — which is also how it gets a real
  certificate.
- **One machine, no redundancy.** Its disk is the system.
- **Nobody else is watching it.** No managed backups, no failover, no patching.
  The Postgres dump in §6 is the difference between an inconvenience and losing
  the work.
