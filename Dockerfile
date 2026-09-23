# skald: a player and world dashboard for Valheim servers.
#
# Python standard library only -- no dependencies to audit, and the image
# stays small. It reads the game's event files and world saves; it never
# writes to them.
FROM python:3.14-slim

RUN useradd --system --uid 10001 --create-home --home-dir /home/skald skald

# Create the mount points in the image, owned as Skald needs them: Docker
# copies an image directory's ownership and mode onto a *fresh* named volume,
# so a first run works with no chown dance. /events is sticky world-writable
# because the game's log hook writes there too, as whichever user the game
# container runs as -- a directory owned by skald alone would shut it out.
# (Bind mounts keep the host's ownership; the README covers that.)
RUN mkdir -p /events /data \
 && chown skald:skald /events /data \
 && chmod 1777 /events \
 && chmod 700 /data

WORKDIR /app
COPY skald/ /app/skald/

ENV PYTHONUNBUFFERED=1 SKALD_PORT=8080
EXPOSE 8080
USER skald

# /healthz answers only if Skald can reach its database, so "healthy" means
# the thing actually works rather than just that a socket is open.
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('SKALD_PORT',os.environ.get('TRACKER_PORT','8080'))+'/healthz', timeout=4).status==200 else 1)"

ENTRYPOINT ["python3", "-m", "skald"]
