#!/usr/bin/env python3

"""Flask API for the RedmineAssistant retrieval service."""

import logging

from flask import Flask, jsonify

from config import ConfigurationError
from database import check_database_connection, get_database_status


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

LOGGER = logging.getLogger("redmine-assistant-api")

app = Flask(__name__)


@app.get("/health")
def health():
    """
    Return process liveness.

    This endpoint deliberately does not check external dependencies.
    """
    return jsonify(
        {
            "status": "ok",
            "service": "redmine-assistant-rag",
        }
    )


@app.get("/health/ready")
def readiness():
    """
    Return application readiness.

    The service is considered ready when PostgreSQL is reachable and the
    database schema can be inspected.
    """
    try:
        check_database_connection()
        database_status = get_database_status()
    except Exception:
        LOGGER.exception("RAG service readiness check failed")

        return jsonify(
            {
                "status": "not_ready",
                "database": "unavailable",
            }
        ), 503

    return jsonify(
        {
            "status": "ready",
            "database": "connected",
            "schema_migrations": database_status["migration_count"],
            "documents": database_status["documents"],
            "chunks": database_status["chunks"],
        }
    )


@app.errorhandler(ConfigurationError)
def configuration_error(error):
    """Return a generic response for application configuration failures."""
    LOGGER.error("Application configuration error: %s", error)

    return jsonify(
        {
            "status": "error",
            "error": "Application configuration is invalid",
        }
    ), 500

