"""The hosted reader (NL-163 Stage-A). A package so the suite reaches it via
`pythonpath = ["."]` and the container runs `gunicorn hosted.app:app` — one
import shape in both places. Deliberately empty: importing this must not
import Flask, so a dev without the hosted dependency still collects the suite.
"""
