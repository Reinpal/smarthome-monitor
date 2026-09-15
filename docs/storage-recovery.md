# External-drive storage and boot guard

## Active deployment

The only active metrics database is `/mnt/external/smarthome-data/prometheus`.
The expected external-drive UUID is private host configuration in `/etc/fstab`;
it is intentionally not recorded here.
The deployed `.env` sets `DATA_PATH=/mnt/external/smarthome-data`.
Prometheus retention remains **5 years**.

Docker's automatic container restart happens outside Compose, so Compose
`depends_on` cannot fix host mount ordering. This Pi uses a systemd drop-in:

- `/etc/systemd/system/docker.service.d/smarthome-storage.conf`, sourced from
  [`deploy/docker-storage.conf`](../deploy/docker-storage.conf).
- `/usr/local/sbin/check-smarthome-storage`, sourced from
  [`deploy/check-smarthome-storage`](../deploy/check-smarthome-storage).

`RequiresMountsFor` and `After` wait for the external mount before Docker starts,
even though `/etc/fstab` uses `nofail`. `ExecStartPre` checks the actual mounted
device, UUID, writable mount and existing database directory. `BindsTo` stops
Docker if systemd deactivates the mount. Docker live restore is disabled on this
Pi; keep it disabled so stopping Docker also stops its containers.

**This deliberately gates all Docker containers on the Pi**, not just Grafana.
A missing, incorrect, or read-only drive must cause an outage, never silently
create a replacement database on the SD card. A drive that does not appear
before systemd's device timeout leaves Docker stopped. After fixing storage:

```sh
sudo systemctl start mnt-external.mount
sudo /usr/local/sbin/check-smarthome-storage
sudo systemctl reset-failed docker.service
sudo systemctl start docker.service
```

Do not bypass the guard by launching `dockerd` manually. A hardware I/O failure
that leaves a filesystem mounted still requires monitoring and backups; this
is a boot-order/fail-closed safeguard, not protection from every disk failure.

## Installing/reinstalling

These settings are Pi-specific. If replacing the drive, restore the database
first, then update the UUID entry in `/etc/fstab`. The tracked guard now reads
that existing host configuration and requires exactly one `UUID=...` source for
`/mnt/external`; paths/labels are rejected rather than guessed. Do not copy
identifiers into the public repository. Previously installed guards are not
changed by editing this checkout; reinstall only during approved maintenance.

```sh
sudo install -m 755 deploy/check-smarthome-storage /usr/local/sbin/check-smarthome-storage
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo install -m 644 deploy/docker-storage.conf /etc/systemd/system/docker.service.d/smarthome-storage.conf
sudo /usr/local/sbin/check-smarthome-storage
sudo systemctl daemon-reload
```

The dependency is effective for the next Docker start. No host reboot is needed
to install it. Compose also sets `create_host_path: false` for `/data`; for a new
installation, explicitly create the data directory on the intended storage
before starting Compose.

Run `sh tests/test_storage_guard.sh` on the Pi to test the correct drive and
simulate missing, wrong and read-only mounts in **private mount namespaces**.
These tests do not unmount or change the actual host drive.

## September 14, 2026 recovery

Docker bound `/mnt/external/smarthome-data` from the SD card at approximately
10:17:40 Vienna time; the external disk mounted at 10:18:03. The container kept
its private bind to the underlying SD directory and began a new database.
The historical database on the external disk had not been deleted.

Recovery procedure performed:

1. Stopped the scraper and LGTM; archived the SD data and external Prometheus
   and Grafana directories under `backups/recovery-20260914/`.
2. Replayed **copies** of both Prometheus databases with the pinned Prometheus
   3.10.0 image and five-year retention, isolated from the network.
3. Created head-inclusive snapshots of both databases. This preserves the old
   WAL's un-compacted samples; simply adding newer blocks to the old database
   could otherwise skip that older WAL data.
4. Compared the short overlap around the outage: zero conflicting
   series/timestamps. The merged query returned all 20,500 samples from the
   comparison window exactly. Prometheus compacted the overlapping blocks.
5. Installed the merged database at the original external-drive path and
   recreated LGTM, restoring the external Grafana database as well.
6. Verified historical queries on August 28, September 6, September 13 and
   September 14, plus post-outage and current data. Retention is `5y`, with
   zero reported WAL corruption or failed compactions.
7. Tested missing, incorrect and read-only disks in private mount namespaces;
   all were rejected. Performed a real Docker service restart and confirmed
   the installed `ExecStartPre` guard succeeded before containers started.
   A full host reboot/power-cut test was not performed.

Originals and recovery working copies are retained as checksum-verified **cold
archives**, not additional active databases. The accidental SD directory and
temporary extracted recovery databases are removed after verification.
Query comparison evidence is also under `backups/recovery-20260914/` (gitignored).

The recovery retains available measurements, not readings that were never
collected during the power outage or the brief maintenance stop. Existing data
before August 28 that had already expired under the former 15-day retention
cannot be recovered by this merge.

Backups on the Pi are not off-device backups. Recurring off-device backups and
a tested restore remain necessary for protection from hardware failure.
