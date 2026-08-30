# The hosted reader. NL-163 Stage-A M2.
#
# SHIPS DARK: the org builds and verifies this image's contents on loopback and
# never deploys. Deployment is the principal's hands (hosted/DEPLOY.md), gated
# on the NL-33 human review.
#
# Build context is the REPO ROOT, so the copy below is `hosted/` and the app is
# imported as `hosted.app` — the same import shape the suite uses, which is why
# a test that passes here passes there:
#
#     docker build -f hosted/Dockerfile -t newslens-hosted .
#
FROM python:3.12-slim

# The product tree's floor is 3.9 (the principal's system Python). This image
# has no such constraint — it runs nothing from src/newslens — so it takes a
# current, supported runtime with current security fixes.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEWSLENS_HOSTED_DATA=/data

WORKDIR /srv

COPY hosted/requirements.txt /srv/hosted/requirements.txt
RUN pip install --no-cache-dir -r /srv/hosted/requirements.txt

COPY hosted /srv/hosted

# Not root. The volume is the only writable path this process needs.
RUN useradd --system --uid 10001 newslens \
    && mkdir -p /data && chown -R newslens:newslens /data
USER newslens

EXPOSE 8080

# Two workers: enough to serve one reader and a push at the same time, few
# enough that SQLite's WAL never sees a crowd.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", \
     "--timeout", "60", "--access-logfile", "-", "hosted.app:app"]
