"""Tests for Procore webhook handler, processor, and registration.

Tests cover:
  1. HMAC-SHA256 signature verification (valid, invalid, missing)
  2. Idempotency deduplication via Redis
  3. Kafka event publishing on webhook receipt
  4. Webhook processor event routing per resource type
  5. Dead letter queue after 3 failed retries
  6. Webhook auto-registration on Procore connection
  7. Webhook unregistration on disconnect
  8. Edge cases: malformed payloads, missing fields, Redis down
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.procore_webhooks import (
    _WEBHOOK_SEEN_PREFIX,
    _WEBHOOK_SEEN_TTL,
    verify_signature,
)
from app.services.integrations.procore_webhook_processor import (
    _RESOURCE_HANDLERS,
    DLQ_TOPIC,
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    WEBHOOK_TOPIC,
    handle_webhook_event,
)

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "test-webhook-secret-key-12345"

SAMPLE_DOCUMENT_EVENT = {
    "resource_name": "Documents",
    "event_type": "create",
    "resource_id": 42001,
    "project_id": 12345,
    "company_id": 99,
    "id": "evt-doc-001",
}

SAMPLE_RFI_EVENT = {
    "resource_name": "RFIs",
    "event_type": "create",
    "resource_id": 42002,
    "project_id": 12345,
    "company_id": 99,
    "id": "evt-rfi-001",
}

SAMPLE_BUDGET_EVENT = {
    "resource_name": "Budget Line Items",
    "event_type": "update",
    "resource_id": 42003,
    "project_id": 12345,
    "company_id": 99,
    "id": "evt-budget-001",
}

SAMPLE_CHANGE_ORDER_EVENT = {
    "resource_name": "Change Orders",
    "event_type": "create",
    "resource_id": 42004,
    "project_id": 12345,
    "company_id": 99,
    "id": "evt-co-001",
}

SAMPLE_DAILY_LOG_EVENT = {
    "resource_name": "Daily Logs",
    "event_type": "create",
    "resource_id": 42005,
    "project_id": 12345,
    "company_id": 99,
    "id": "evt-dl-001",
}

SAMPLE_SUBMITTAL_EVENT = {
    "resource_name": "Submittals",
    "event_type": "create",
    "resource_id": 42006,
    "project_id": 12345,
    "company_id": 99,
}

SAMPLE_OBSERVATION_EVENT = {
    "resource_name": "Observations",
    "event_type": "create",
    "resource_id": 42007,
    "project_id": 12345,
    "company_id": 99,
}


def _sign_payload(payload: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for a payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()


def _make_cloudevent(procore_event: dict) -> dict:
    """Wrap a Procore event in a CloudEvents envelope."""
    return {
        "specversion": "1.0",
        "id": str(uuid.uuid4()),
        "type": f"constructai.procore.{procore_event['resource_name']}.{procore_event['event_type']}",
        "source": "/procore-webhook",
        "time": datetime.now(UTC).isoformat(),
        "datacontenttype": "application/json",
        "data": {
            "resource_name": procore_event["resource_name"],
            "event_type": procore_event["event_type"],
            "resource_id": procore_event.get("resource_id"),
            "project_id": procore_event.get("project_id"),
            "company_id": procore_event.get("company_id"),
            "delivery_id": procore_event.get("id", ""),
            "payload": procore_event,
        },
    }


# ===========================================================================
# Signature Verification
# ===========================================================================


class TestSignatureVerification:
    """Test HMAC-SHA256 signature verification."""

    def test_valid_signature(self):
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        sig = _sign_payload(payload, WEBHOOK_SECRET)
        assert verify_signature(payload, sig, WEBHOOK_SECRET) is True

    def test_invalid_signature(self):
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        assert verify_signature(payload, "invalid-sig", WEBHOOK_SECRET) is False

    def test_empty_signature(self):
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        assert verify_signature(payload, "", WEBHOOK_SECRET) is False

    def test_tampered_payload(self):
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        sig = _sign_payload(payload, WEBHOOK_SECRET)
        tampered = payload + b"extra"
        assert verify_signature(tampered, sig, WEBHOOK_SECRET) is False

    def test_wrong_secret(self):
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        sig = _sign_payload(payload, WEBHOOK_SECRET)
        assert verify_signature(payload, sig, "wrong-secret") is False

    def test_empty_secret_rejects_webhook(self):
        """When PROCORE_WEBHOOK_SECRET is empty, webhooks are rejected for security."""
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        assert verify_signature(payload, "anything", "") is False

    def test_different_payloads_different_signatures(self):
        payload1 = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        payload2 = json.dumps(SAMPLE_RFI_EVENT).encode()
        sig1 = _sign_payload(payload1, WEBHOOK_SECRET)
        sig2 = _sign_payload(payload2, WEBHOOK_SECRET)
        assert sig1 != sig2

    def test_signature_is_deterministic(self):
        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        sig1 = _sign_payload(payload, WEBHOOK_SECRET)
        sig2 = _sign_payload(payload, WEBHOOK_SECRET)
        assert sig1 == sig2


# ===========================================================================
# Webhook Endpoint (handler)
# ===========================================================================


class TestWebhookEndpoint:
    """Test the POST /webhooks/procore endpoint."""

    @pytest.fixture
    def mock_request(self):
        """Create a mock FastAPI request."""
        request = AsyncMock()
        return request

    def _make_request(
        self, event: dict, secret: str = WEBHOOK_SECRET, delivery_id: str = "del-123"
    ):
        """Create a mock request with proper signature."""
        payload = json.dumps(event).encode()
        sig = _sign_payload(payload, secret) if secret else ""
        request = AsyncMock()
        request.body = AsyncMock(return_value=payload)
        request.headers = {
            "X-Procore-Signature": sig,
            "X-Procore-Delivery-Id": delivery_id,
        }
        return request

    @pytest.mark.asyncio
    async def test_valid_webhook_returns_200(self):
        """A properly signed webhook returns 200."""
        from app.api.v1.procore_webhooks import procore_webhook

        request = self._make_request(SAMPLE_DOCUMENT_EVENT)

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.api.v1.procore_webhooks._get_kafka_producer", return_value=None),
            patch("app.services.cache.CacheService") as MockCache,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            cache_instance = AsyncMock()
            cache_instance._ensure_client = AsyncMock(return_value=True)
            cache_instance._client = AsyncMock()
            cache_instance._client.set = AsyncMock(return_value=True)  # NX returns True (new key)
            MockCache.return_value = cache_instance

            response = await procore_webhook(request)
            assert response.status_code == 200
            assert b"accepted" in response.body

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self):
        """A webhook with wrong signature returns 401."""
        from app.api.v1.procore_webhooks import procore_webhook

        payload = json.dumps(SAMPLE_DOCUMENT_EVENT).encode()
        request = AsyncMock()
        request.body = AsyncMock(return_value=payload)
        request.headers = {
            "X-Procore-Signature": "bad-signature",
            "X-Procore-Delivery-Id": "del-456",
        }

        with patch("app.api.v1.procore_webhooks.settings") as mock_settings:
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET

            response = await procore_webhook(request)
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_duplicate_delivery_returns_200_duplicate(self):
        """A duplicate delivery ID returns 200 with 'duplicate' status."""
        from app.api.v1.procore_webhooks import procore_webhook

        request = self._make_request(SAMPLE_DOCUMENT_EVENT, delivery_id="dup-001")

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.api.v1.procore_webhooks._get_kafka_producer", return_value=None),
            patch("app.services.cache.CacheService") as MockCache,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            cache_instance = AsyncMock()
            cache_instance._ensure_client = AsyncMock(return_value=True)
            cache_instance._client = AsyncMock()
            cache_instance._client.set = AsyncMock(
                return_value=False
            )  # NX returns False (already seen)
            MockCache.return_value = cache_instance

            response = await procore_webhook(request)
            assert response.status_code == 200
            assert b"duplicate" in response.body

    @pytest.mark.asyncio
    async def test_kafka_publish_called(self):
        """Webhook publishes event to Kafka."""
        from app.api.v1.procore_webhooks import procore_webhook

        request = self._make_request(SAMPLE_RFI_EVENT)
        mock_producer = AsyncMock()
        mock_producer.publish = AsyncMock(return_value="event-id-123")

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.api.v1.procore_webhooks._get_kafka_producer", return_value=mock_producer),
            patch("app.services.cache.CacheService") as MockCache,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            cache_instance = AsyncMock()
            cache_instance._ensure_client = AsyncMock(return_value=True)
            cache_instance._client = AsyncMock()
            cache_instance._client.set = AsyncMock(return_value=True)
            MockCache.return_value = cache_instance

            response = await procore_webhook(request)
            assert response.status_code == 200

            mock_producer.publish.assert_called_once()
            call_kwargs = mock_producer.publish.call_args
            assert "constructai.procore.RFIs.create" in call_kwargs.kwargs["event_type"]
            assert call_kwargs.kwargs["source"] == "/procore-webhook"

    @pytest.mark.asyncio
    async def test_malformed_json_returns_400(self):
        """Malformed JSON body returns 400."""
        from app.api.v1.procore_webhooks import procore_webhook

        bad_payload = b"not valid json {"
        sig = _sign_payload(bad_payload, WEBHOOK_SECRET)
        request = AsyncMock()
        request.body = AsyncMock(return_value=bad_payload)
        request.headers = {
            "X-Procore-Signature": sig,
            "X-Procore-Delivery-Id": "del-bad",
        }

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.services.cache.CacheService") as MockCache,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            cache_instance = AsyncMock()
            cache_instance._ensure_client = AsyncMock(return_value=True)
            cache_instance._client = AsyncMock()
            cache_instance._client.set = AsyncMock(return_value=True)
            MockCache.return_value = cache_instance

            response = await procore_webhook(request)
            assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_redis_down_still_processes(self):
        """When Redis is unavailable, the webhook returns 503 so Procore
        retries; the LRU fallback only handles short blips and we don't
        want to risk processing duplicates without durable dedup."""
        from app.api.v1.procore_webhooks import procore_webhook

        request = self._make_request(SAMPLE_DOCUMENT_EVENT)

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.api.v1.procore_webhooks._get_kafka_producer", return_value=None),
            patch("app.services.cache.CacheService") as MockCache,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            cache_instance = AsyncMock()
            cache_instance._ensure_client = AsyncMock(return_value=False)
            cache_instance._client = None
            MockCache.return_value = cache_instance

            response = await procore_webhook(request)
            assert response.status_code == 503

    @pytest.mark.asyncio
    async def test_no_delivery_id_skips_dedup(self):
        """When no delivery ID is provided, dedup is skipped."""
        from app.api.v1.procore_webhooks import procore_webhook

        request = self._make_request(SAMPLE_DOCUMENT_EVENT, delivery_id="")

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.api.v1.procore_webhooks._get_kafka_producer", return_value=None),
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET

            response = await procore_webhook(request)
            assert response.status_code == 200
            assert b"accepted" in response.body

    @pytest.mark.asyncio
    async def test_redis_key_uses_correct_prefix_and_ttl(self):
        """Redis dedup uses the correct key prefix and TTL."""
        from app.api.v1.procore_webhooks import procore_webhook

        delivery_id = "del-ttl-check"
        request = self._make_request(SAMPLE_DOCUMENT_EVENT, delivery_id=delivery_id)

        with (
            patch("app.api.v1.procore_webhooks.settings") as mock_settings,
            patch("app.api.v1.procore_webhooks._get_kafka_producer", return_value=None),
            patch("app.services.cache.CacheService") as MockCache,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            cache_instance = AsyncMock()
            cache_instance._ensure_client = AsyncMock(return_value=True)
            mock_client = AsyncMock()
            mock_client.set = AsyncMock(return_value=True)
            cache_instance._client = mock_client
            MockCache.return_value = cache_instance

            await procore_webhook(request)

            mock_client.set.assert_called_once_with(
                f"{_WEBHOOK_SEEN_PREFIX}{delivery_id}",
                "1",
                ex=_WEBHOOK_SEEN_TTL,
                nx=True,
            )


# ===========================================================================
# Webhook Processor (event routing)
# ===========================================================================


class TestWebhookProcessor:
    """Test the webhook event processor routing logic."""

    def test_resource_handlers_registered(self):
        """All expected resource types have handlers."""
        expected = {
            "Documents",
            "RFIs",
            "Budget Line Items",
            "Change Orders",
            "Daily Logs",
            "Submittals",
            "Observations",
        }
        assert set(_RESOURCE_HANDLERS.keys()) == expected

    @pytest.mark.asyncio
    async def test_document_event_triggers_sync_and_reindex(self):
        """Document create triggers document sync and RAG re-indexing."""
        project_id = uuid.uuid4()
        org_id = uuid.uuid4()
        event = _make_cloudevent(SAMPLE_DOCUMENT_EVENT)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value={"id": project_id, "org_id": org_id},
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._get_db_session",
            ) as mock_db_session,
            patch(
                "app.services.integrations.procore_sync.sync_documents",
                new_callable=AsyncMock,
                return_value={"synced": 1, "errors": []},
            ) as mock_sync,
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
            patch(
                "app.services.integrations.procore_api.ProcoreAPI",
            ),
        ):
            mock_session = AsyncMock()
            mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_webhook_event(event)

            mock_sync.assert_called_once()
            mock_publish.assert_called_once()
            publish_call = mock_publish.call_args
            assert publish_call.kwargs["event_type"] == "constructai.document.reindex_requested"

    @pytest.mark.asyncio
    async def test_rfi_create_triggers_resolution_agent(self):
        """RFI create triggers sync AND RFI Resolution Agent."""
        project_id = uuid.uuid4()
        org_id = uuid.uuid4()
        event = _make_cloudevent(SAMPLE_RFI_EVENT)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value={"id": project_id, "org_id": org_id},
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._get_db_session",
            ) as mock_db_session,
            patch(
                "app.services.integrations.procore_sync.sync_rfis",
                new_callable=AsyncMock,
                return_value={"synced": 1, "errors": []},
            ) as mock_sync,
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
            patch(
                "app.services.integrations.procore_api.ProcoreAPI",
            ),
        ):
            mock_session = AsyncMock()
            mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_webhook_event(event)

            mock_sync.assert_called_once()
            mock_publish.assert_called_once()
            publish_call = mock_publish.call_args
            assert "resolution_requested" in publish_call.kwargs["event_type"]

    @pytest.mark.asyncio
    async def test_rfi_update_does_not_trigger_resolution(self):
        """RFI update syncs but does NOT trigger Resolution Agent."""
        project_id = uuid.uuid4()
        org_id = uuid.uuid4()
        rfi_update = {**SAMPLE_RFI_EVENT, "event_type": "update"}
        event = _make_cloudevent(rfi_update)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value={"id": project_id, "org_id": org_id},
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._get_db_session",
            ) as mock_db_session,
            patch(
                "app.services.integrations.procore_sync.sync_rfis",
                new_callable=AsyncMock,
                return_value={"synced": 1, "errors": []},
            ) as mock_sync,
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
            patch(
                "app.services.integrations.procore_api.ProcoreAPI",
            ),
        ):
            mock_session = AsyncMock()
            mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_webhook_event(event)

            mock_sync.assert_called_once()
            mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_budget_event_triggers_evm_recalc(self):
        """Budget update triggers sync and EVM recalculation."""
        project_id = uuid.uuid4()
        org_id = uuid.uuid4()
        event = _make_cloudevent(SAMPLE_BUDGET_EVENT)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value={"id": project_id, "org_id": org_id},
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._get_db_session",
            ) as mock_db_session,
            patch(
                "app.services.integrations.procore_sync.sync_budget",
                new_callable=AsyncMock,
                return_value={"synced": 5, "errors": []},
            ) as mock_sync,
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
            patch(
                "app.services.integrations.procore_api.ProcoreAPI",
            ),
        ):
            mock_session = AsyncMock()
            mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_webhook_event(event)

            mock_sync.assert_called_once()
            mock_publish.assert_called_once()
            publish_call = mock_publish.call_args
            assert "evm_recalculation_requested" in publish_call.kwargs["event_type"]

    @pytest.mark.asyncio
    async def test_change_order_triggers_analyzer(self):
        """Change order create triggers sync and CO Analyzer."""
        project_id = uuid.uuid4()
        org_id = uuid.uuid4()
        event = _make_cloudevent(SAMPLE_CHANGE_ORDER_EVENT)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value={"id": project_id, "org_id": org_id},
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._get_db_session",
            ) as mock_db_session,
            patch(
                "app.services.integrations.procore_sync.sync_change_orders",
                new_callable=AsyncMock,
                return_value={"synced": 1, "errors": []},
            ) as mock_sync,
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
            patch(
                "app.services.integrations.procore_api.ProcoreAPI",
            ),
        ):
            mock_session = AsyncMock()
            mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_webhook_event(event)

            mock_sync.assert_called_once()
            mock_publish.assert_called_once()
            publish_call = mock_publish.call_args
            assert "analysis_requested" in publish_call.kwargs["event_type"]

    @pytest.mark.asyncio
    async def test_daily_log_syncs_without_downstream(self):
        """Daily log event syncs but has no downstream trigger."""
        project_id = uuid.uuid4()
        org_id = uuid.uuid4()
        event = _make_cloudevent(SAMPLE_DAILY_LOG_EVENT)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value={"id": project_id, "org_id": org_id},
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._get_db_session",
            ) as mock_db_session,
            patch(
                "app.services.integrations.procore_sync.sync_daily_logs",
                new_callable=AsyncMock,
                return_value={"synced": 1, "errors": []},
            ) as mock_sync,
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
            patch(
                "app.services.integrations.procore_api.ProcoreAPI",
            ),
        ):
            mock_session = AsyncMock()
            mock_db_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await handle_webhook_event(event)

            mock_sync.assert_called_once()
            mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_submittal_event_logged_only(self):
        """Submittal events are logged but not synced."""
        event = _make_cloudevent(SAMPLE_SUBMITTAL_EVENT)
        # Should not raise, just log
        await handle_webhook_event(event)

    @pytest.mark.asyncio
    async def test_observation_event_logged_only(self):
        """Observation events are logged but not synced."""
        event = _make_cloudevent(SAMPLE_OBSERVATION_EVENT)
        await handle_webhook_event(event)

    @pytest.mark.asyncio
    async def test_unknown_resource_ignored(self):
        """Unknown resource types are acknowledged but not processed."""
        event = _make_cloudevent(
            {
                "resource_name": "UnknownType",
                "event_type": "create",
                "resource_id": 1,
                "project_id": 1,
                "company_id": 1,
            }
        )
        # Should not raise
        await handle_webhook_event(event)

    @pytest.mark.asyncio
    async def test_missing_project_skips_sync(self):
        """When no local project matches, sync is skipped."""
        event = _make_cloudevent(SAMPLE_DOCUMENT_EVENT)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "app.services.integrations.procore_webhook_processor._publish_downstream",
                new_callable=AsyncMock,
            ) as mock_publish,
        ):
            await handle_webhook_event(event)
            mock_publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_event_type_ignored(self):
        """Delete events are ignored for documents."""
        event = _make_cloudevent(
            {
                **SAMPLE_DOCUMENT_EVENT,
                "event_type": "delete",
            }
        )

        with patch(
            "app.services.integrations.procore_webhook_processor._get_project_by_procore_id",
            new_callable=AsyncMock,
        ) as mock_lookup:
            await handle_webhook_event(event)
            mock_lookup.assert_not_called()


# ===========================================================================
# Dead Letter Queue
# ===========================================================================


class TestWebhookDLQ:
    """Test dead letter queue after failed retries."""

    @pytest.mark.asyncio
    async def test_dlq_after_max_retries(self):
        """Message is forwarded to DLQ after MAX_RETRIES failures."""
        from app.services.integrations.procore_webhook_processor import (
            ProcoreWebhookConsumer,
        )

        consumer = ProcoreWebhookConsumer()
        consumer._dlq_producer = MagicMock()
        consumer._consumer = MagicMock()

        # Create a message that will always fail to process
        bad_event = {"specversion": "1.0", "type": "bad", "data": {}}
        raw = json.dumps(bad_event).encode()
        msg = MagicMock()
        msg.value.return_value = raw

        with (
            patch(
                "app.services.integrations.procore_webhook_processor.handle_webhook_event",
                new_callable=AsyncMock,
                side_effect=Exception("processing failed"),
            ),
            patch(
                "app.services.integrations.procore_webhook_processor.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            await consumer._process_with_retry(msg)

        # Should have been forwarded to DLQ
        consumer._dlq_producer.produce.assert_called_once()
        call_kwargs = consumer._dlq_producer.produce.call_args
        assert call_kwargs.kwargs["topic"] == DLQ_TOPIC

    @pytest.mark.asyncio
    async def test_retry_backoff_exponential(self):
        """Verify exponential backoff between retries."""
        from app.services.integrations.procore_webhook_processor import (
            ProcoreWebhookConsumer,
        )

        consumer = ProcoreWebhookConsumer()
        consumer._dlq_producer = MagicMock()
        consumer._consumer = MagicMock()

        bad_event = {"specversion": "1.0", "type": "bad", "data": {}}
        raw = json.dumps(bad_event).encode()
        msg = MagicMock()
        msg.value.return_value = raw

        sleep_calls = []

        async def track_sleep(duration):
            sleep_calls.append(duration)

        with (
            patch(
                "app.services.integrations.procore_webhook_processor.handle_webhook_event",
                new_callable=AsyncMock,
                side_effect=Exception("fail"),
            ),
            patch(
                "app.services.integrations.procore_webhook_processor.asyncio.sleep",
                side_effect=track_sleep,
            ),
        ):
            await consumer._process_with_retry(msg)

        # 3 attempts: backoff after attempt 1 and 2 (not after 3 which goes to DLQ)
        # M-27: backoff is RETRY_BACKOFF_BASE**attempt + up to 1s jitter, so
        # check the values fall in the expected window rather than equality.
        assert len(sleep_calls) == 2
        assert RETRY_BACKOFF_BASE**1 <= sleep_calls[0] < RETRY_BACKOFF_BASE**1 + 1
        assert RETRY_BACKOFF_BASE**2 <= sleep_calls[1] < RETRY_BACKOFF_BASE**2 + 1

    @pytest.mark.asyncio
    async def test_successful_retry_does_not_dlq(self):
        """If processing succeeds on retry, no DLQ."""
        from app.services.integrations.procore_webhook_processor import (
            ProcoreWebhookConsumer,
        )

        consumer = ProcoreWebhookConsumer()
        consumer._dlq_producer = MagicMock()
        consumer._consumer = MagicMock()

        event = _make_cloudevent(SAMPLE_SUBMITTAL_EVENT)
        raw = json.dumps(event).encode()
        msg = MagicMock()
        msg.value.return_value = raw

        call_count = 0

        async def fail_then_succeed(evt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("transient failure")
            # Succeeds on second call

        with (
            patch(
                "app.services.integrations.procore_webhook_processor.handle_webhook_event",
                new_callable=AsyncMock,
                side_effect=fail_then_succeed,
            ),
            patch(
                "app.services.integrations.procore_webhook_processor.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            await consumer._process_with_retry(msg)

        consumer._dlq_producer.produce.assert_not_called()
        consumer._consumer.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_includes_error_headers(self):
        """DLQ message includes original topic and error reason."""
        from app.services.integrations.procore_webhook_processor import (
            ProcoreWebhookConsumer,
        )

        consumer = ProcoreWebhookConsumer()
        consumer._dlq_producer = MagicMock()
        consumer._consumer = MagicMock()

        bad_event = {"specversion": "1.0", "type": "bad", "data": {}}
        raw = json.dumps(bad_event).encode()
        msg = MagicMock()
        msg.value.return_value = raw

        with (
            patch(
                "app.services.integrations.procore_webhook_processor.handle_webhook_event",
                new_callable=AsyncMock,
                side_effect=Exception("db connection lost"),
            ),
            patch(
                "app.services.integrations.procore_webhook_processor.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            await consumer._process_with_retry(msg)

        call_kwargs = consumer._dlq_producer.produce.call_args.kwargs
        headers = dict(call_kwargs["headers"])
        assert headers["dlq.original.topic"] == WEBHOOK_TOPIC.encode("utf-8")
        assert b"db connection lost" in headers["dlq.error.reason"]


# ===========================================================================
# Webhook Registration
# ===========================================================================


class TestWebhookRegistration:
    """Test webhook auto-registration."""

    @pytest.mark.asyncio
    async def test_registers_all_resource_types(self):
        """All 7 resource types are registered."""
        from app.services.integrations.procore_webhooks import (
            WEBHOOK_RESOURCES,
            register_webhooks,
        )

        org_id = uuid.uuid4()
        company_id = 99
        mock_db = AsyncMock()

        with (
            patch("app.services.integrations.procore_webhooks.settings") as mock_settings,
            patch("app.services.integrations.procore_webhooks.ProcoreAPI") as MockAPI,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            mock_settings.PROCORE_REDIRECT_URI = (
                "http://localhost:8000/api/v1/integrations/procore/callback"
            )

            api_instance = AsyncMock()
            api_instance.list_webhooks = AsyncMock(return_value=[])
            api_instance.register_webhook = AsyncMock(return_value={"id": 1})
            MockAPI.return_value = api_instance

            result = await register_webhooks(org_id, company_id, mock_db)

            assert len(result["registered"]) == len(WEBHOOK_RESOURCES)
            assert result["errors"] == []
            assert result["skipped"] == []
            assert api_instance.register_webhook.call_count == len(WEBHOOK_RESOURCES)

    @pytest.mark.asyncio
    async def test_skips_already_registered(self):
        """Existing webhooks for the same destination are skipped."""
        from app.services.integrations.procore_webhooks import register_webhooks

        org_id = uuid.uuid4()
        company_id = 99
        mock_db = AsyncMock()
        dest_url = "http://localhost:8000/api/v1/webhooks/procore"

        existing_hooks = [
            {"namespace": "Documents", "destination_url": dest_url, "id": 10},
            {"namespace": "RFIs", "destination_url": dest_url, "id": 11},
        ]

        with (
            patch("app.services.integrations.procore_webhooks.settings") as mock_settings,
            patch("app.services.integrations.procore_webhooks.ProcoreAPI") as MockAPI,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            mock_settings.PROCORE_REDIRECT_URI = (
                "http://localhost:8000/api/v1/integrations/procore/callback"
            )

            api_instance = AsyncMock()
            api_instance.list_webhooks = AsyncMock(return_value=existing_hooks)
            api_instance.register_webhook = AsyncMock(return_value={"id": 12})
            MockAPI.return_value = api_instance

            result = await register_webhooks(org_id, company_id, mock_db)

            assert "Documents" in result["skipped"]
            assert "RFIs" in result["skipped"]
            assert len(result["skipped"]) == 2
            # 7 total - 2 already registered = 5 new
            assert len(result["registered"]) == 5

    @pytest.mark.asyncio
    async def test_registration_error_per_resource(self):
        """Individual resource registration failures don't abort others."""
        from app.services.integrations.procore_webhooks import register_webhooks

        org_id = uuid.uuid4()
        company_id = 99
        mock_db = AsyncMock()

        call_count = 0

        async def register_with_one_failure(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("API error on third registration")
            return {"id": call_count}

        with (
            patch("app.services.integrations.procore_webhooks.settings") as mock_settings,
            patch("app.services.integrations.procore_webhooks.ProcoreAPI") as MockAPI,
        ):
            mock_settings.PROCORE_WEBHOOK_SECRET = WEBHOOK_SECRET
            mock_settings.PROCORE_REDIRECT_URI = (
                "http://localhost:8000/api/v1/integrations/procore/callback"
            )

            api_instance = AsyncMock()
            api_instance.list_webhooks = AsyncMock(return_value=[])
            api_instance.register_webhook = AsyncMock(side_effect=register_with_one_failure)
            MockAPI.return_value = api_instance

            result = await register_webhooks(org_id, company_id, mock_db)

            assert len(result["registered"]) == 6  # 7 - 1 failed
            assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_no_secret_skips_registration(self):
        """When PROCORE_WEBHOOK_SECRET is empty, registration is skipped."""
        from app.services.integrations.procore_webhooks import register_webhooks

        org_id = uuid.uuid4()
        mock_db = AsyncMock()

        with patch("app.services.integrations.procore_webhooks.settings") as mock_settings:
            mock_settings.PROCORE_WEBHOOK_SECRET = ""

            result = await register_webhooks(org_id, 99, mock_db)

            assert result["registered"] == []
            assert result["skipped"] == []
            assert result["errors"] == []


class TestWebhookUnregistration:
    """Test webhook cleanup on disconnect."""

    @pytest.mark.asyncio
    async def test_unregister_deletes_matching_hooks(self):
        """Unregister removes all hooks with our destination URL."""
        from app.services.integrations.procore_webhooks import unregister_webhooks

        org_id = uuid.uuid4()
        company_id = 99
        mock_db = AsyncMock()
        dest_url = "http://localhost:8000/api/v1/webhooks/procore"

        existing_hooks = [
            {"id": 10, "namespace": "Documents", "destination_url": dest_url},
            {"id": 11, "namespace": "RFIs", "destination_url": dest_url},
            {"id": 99, "namespace": "Other", "destination_url": "https://other.com/hook"},
        ]

        with (
            patch("app.services.integrations.procore_webhooks.settings") as mock_settings,
            patch("app.services.integrations.procore_webhooks.ProcoreAPI") as MockAPI,
        ):
            mock_settings.PROCORE_REDIRECT_URI = (
                "http://localhost:8000/api/v1/integrations/procore/callback"
            )

            api_instance = AsyncMock()
            api_instance.list_webhooks = AsyncMock(return_value=existing_hooks)
            api_instance.delete_webhook = AsyncMock()
            MockAPI.return_value = api_instance

            deleted = await unregister_webhooks(org_id, company_id, mock_db)

            assert deleted == 2
            assert api_instance.delete_webhook.call_count == 2


# ===========================================================================
# ProcoreAPI webhook methods
# ===========================================================================


class TestProcoreAPIWebhookMethods:
    """Test the webhook API methods on ProcoreAPI."""

    def _make_api(self):
        """Create a ProcoreAPI instance with mocked dependencies."""
        from app.services.integrations.procore_api import ProcoreAPI

        org_id = uuid.uuid4()
        db = AsyncMock()
        api = ProcoreAPI(org_id=org_id, db=db)
        return api

    @pytest.mark.asyncio
    async def test_register_webhook_calls_v1_1(self):
        """register_webhook POSTs to /companies/{id}/webhooks/hooks."""
        api = self._make_api()

        with patch.object(api, "_request_v1_1", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": 42, "namespace": "Documents"}

            result = await api.register_webhook(
                company_id=99,
                destination_url="https://example.com/hook",
                resource_name="Documents",
            )

            assert result["id"] == 42
            mock_req.assert_called_once_with(
                "POST",
                "/companies/99/webhooks/hooks",
                company_id=99,
                json_body={
                    "hook": {
                        "api_version": "v2",
                        "destination_url": "https://example.com/hook",
                        "namespace": "Documents",
                    },
                },
            )

    @pytest.mark.asyncio
    async def test_list_webhooks(self):
        """list_webhooks GETs from /companies/{id}/webhooks/hooks."""
        api = self._make_api()

        with patch.object(api, "_request_v1_1", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = [{"id": 1}, {"id": 2}]

            result = await api.list_webhooks(company_id=99)

            assert len(result) == 2
            mock_req.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_webhook(self):
        """delete_webhook DELETEs a specific hook."""
        api = self._make_api()

        with patch.object(api, "_request_v1_1", new_callable=AsyncMock) as mock_req:
            await api.delete_webhook(company_id=99, hook_id=42)

            mock_req.assert_called_once_with(
                "DELETE",
                "/companies/99/webhooks/hooks/42",
                company_id=99,
            )


# ===========================================================================
# Kafka topic routing
# ===========================================================================


class TestKafkaTopicRouting:
    """Test that Procore webhook events route to the correct Kafka topic."""

    def test_procore_event_routes_to_webhook_topic(self):
        """constructai.procore.* events go to procore.webhooks topic."""
        from app.services.messaging.kafka_producer import _resolve_topic

        assert _resolve_topic("constructai.procore.Documents.create") == "procore.webhooks"
        assert _resolve_topic("constructai.procore.RFIs.update") == "procore.webhooks"
        assert _resolve_topic("constructai.procore.Budget Line Items.update") == "procore.webhooks"

    def test_other_events_unaffected(self):
        """Other event types still route correctly."""
        from app.services.messaging.kafka_producer import _resolve_topic

        assert _resolve_topic("constructai.safety.incident") == "constructai.safety"
        assert _resolve_topic("constructai.document.ingested") == "constructai.documents"
        assert _resolve_topic("constructai.controls.evm") == "constructai.controls"

    def test_default_topic_still_works(self):
        """Unknown event types go to the default topic."""
        from app.services.messaging.kafka_producer import DEFAULT_TOPIC, _resolve_topic

        assert _resolve_topic("some.random.event") == DEFAULT_TOPIC


# ===========================================================================
# Edge Cases
# ===========================================================================


class TestWebhookEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_missing_project_id_in_event(self):
        """Events without project_id are handled gracefully."""
        event = _make_cloudevent(
            {
                "resource_name": "Documents",
                "event_type": "create",
                "resource_id": 1,
                "project_id": None,
                "company_id": 99,
            }
        )

        # Should not raise
        await handle_webhook_event(event)

    @pytest.mark.asyncio
    async def test_missing_company_id_in_event(self):
        """Events without company_id are handled gracefully."""
        event = _make_cloudevent(
            {
                "resource_name": "RFIs",
                "event_type": "create",
                "resource_id": 1,
                "project_id": 123,
                "company_id": None,
            }
        )

        await handle_webhook_event(event)

    @pytest.mark.asyncio
    async def test_empty_data_in_event(self):
        """Events with empty data are handled gracefully."""
        event = {
            "specversion": "1.0",
            "id": str(uuid.uuid4()),
            "type": "constructai.procore.unknown.test",
            "source": "/test",
            "data": {},
        }
        await handle_webhook_event(event)

    def test_webhook_constants(self):
        """Verify webhook configuration constants."""
        assert MAX_RETRIES == 3
        assert RETRY_BACKOFF_BASE == 2
        assert WEBHOOK_TOPIC == "procore.webhooks"
        assert DLQ_TOPIC == "procore.webhooks.dlq"

    def test_seen_ttl_is_24_hours(self):
        """Idempotency TTL is 24 hours."""
        assert _WEBHOOK_SEEN_TTL == 86_400

    def test_seen_prefix(self):
        """Idempotency key uses correct prefix."""
        assert _WEBHOOK_SEEN_PREFIX == "procore:webhook:seen:"
