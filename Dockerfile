# skald: a player and world dashboard for Valheim servers.
#
# Python standard library only -- no dependencies to audit, and the image
# stays small. It reads the game's event files and world saves; it never
# writes to them.
FROM python:3.14-slim

WORKDIR /app
COPY skald/ /app/skald/

ENV PYTHONUNBUFFERED=1 TRACKER_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('TRACKER_PORT','8080')+'/healthz', timeout=4).status==200 else 1)"

ENTRYPOINT ["python3", "-m", "skald"]
