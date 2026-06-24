import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from app.pipeline.splunk_client import SplunkClient, MAX_RETRIES
from app.config import Settings


@pytest.fixture
def settings():
    return Settings(
        SPLUNK_HOST="localhost",
        SPLUNK_PORT=8089,
        SPLUNK_USERNAME="admin",
        SPLUNK_PASSWORD="changeme",
        SPLUNK_VERIFY_SSL=False,
        LLM_API_KEY="x",
    )


@pytest.fixture
def client(settings):
    return SplunkClient(settings)


def test_client_initializes_with_settings(client, settings):
    assert client.base_url == "https://localhost:8089"
    assert client.auth == ("admin", "changeme")


@pytest.mark.asyncio
async def test_execute_spl_success(client):
    mock_response_create = MagicMock()
    mock_response_create.status_code = 201
    mock_response_create.json.return_value = {"sid": "123"}

    mock_response_status = MagicMock()
    mock_response_status.status_code = 200
    mock_response_status.json.return_value = {"entry": [{"content": {"dispatchState": "DONE"}}]}

    mock_response_results = MagicMock()
    mock_response_results.status_code = 200
    mock_response_results.json.return_value = {
        "results": [{"Store": "A", "sum(revenue)": "15000"}]
    }

    with patch.object(client, "_client") as mock_client:
        mock_client.post = AsyncMock(return_value=mock_response_create)
        mock_client.get = AsyncMock(side_effect=[mock_response_status, mock_response_results])

        results = await client.execute_spl('index=chocolate_index sourcetype=sales | stats sum(revenue) by store_id')
        assert results == [{"Store": "A", "sum(revenue)": "15000"}]


@pytest.mark.asyncio
async def test_execute_spl_retries_on_connect_error(client):
    mock_response_create = MagicMock()
    mock_response_create.json.return_value = {"sid": "123"}
    mock_response_status = MagicMock()
    mock_response_status.json.return_value = {"entry": [{"content": {"dispatchState": "DONE"}}]}
    mock_response_results = MagicMock()
    mock_response_results.json.return_value = {"results": [{"count": "1"}]}

    call_count = 0

    async def post_with_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Connection refused")
        return mock_response_create

    with patch.object(client, "_client") as mock_client:
        mock_client.post = post_with_retry
        mock_client.get = AsyncMock(side_effect=[mock_response_status, mock_response_results])

        with patch("app.pipeline.splunk_client._async_sleep", new_callable=AsyncMock):
            results = await client.execute_spl("index=test | stats count")

    assert results == [{"count": "1"}]
    assert call_count == 2


@pytest.mark.asyncio
async def test_execute_spl_fails_after_max_retries(client):
    with patch.object(client, "_client") as mock_client:
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("app.pipeline.splunk_client._async_sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match=f"after {MAX_RETRIES} attempts"):
                await client.execute_spl("index=test | stats count")

    assert mock_client.post.call_count == MAX_RETRIES


@pytest.mark.asyncio
async def test_execute_spl_no_retry_on_runtime_error(client):
    mock_response_create = MagicMock()
    mock_response_create.json.return_value = {"sid": "123"}
    mock_response_status = MagicMock()
    mock_response_status.json.return_value = {"entry": [{"content": {"dispatchState": "FAILED"}}]}

    with patch.object(client, "_client") as mock_client:
        mock_client.post = AsyncMock(return_value=mock_response_create)
        mock_client.get = AsyncMock(return_value=mock_response_status)

        with pytest.raises(RuntimeError, match="search job failed"):
            await client.execute_spl("index=test | stats count")

    # Should only attempt once — RuntimeError is not retryable
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_execute_spl_retries_on_read_timeout(client):
    """ReadTimeout is a retryable exception."""
    mock_response_create = MagicMock()
    mock_response_create.json.return_value = {"sid": "123"}
    mock_response_status = MagicMock()
    mock_response_status.json.return_value = {"entry": [{"content": {"dispatchState": "DONE"}}]}
    mock_response_results = MagicMock()
    mock_response_results.json.return_value = {"results": [{"count": "1"}]}

    call_count = 0

    async def post_with_timeout(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ReadTimeout("Read timed out")
        return mock_response_create

    with patch.object(client, "_client") as mock_client:
        mock_client.post = post_with_timeout
        mock_client.get = AsyncMock(side_effect=[mock_response_status, mock_response_results])

        with patch("app.pipeline.splunk_client._async_sleep", new_callable=AsyncMock):
            results = await client.execute_spl("index=test | stats count")

    assert results == [{"count": "1"}]
    assert call_count == 2


@pytest.mark.asyncio
async def test_execute_spl_retries_on_pool_timeout(client):
    """PoolTimeout is a retryable exception."""
    mock_response_create = MagicMock()
    mock_response_create.json.return_value = {"sid": "456"}
    mock_response_status = MagicMock()
    mock_response_status.json.return_value = {"entry": [{"content": {"dispatchState": "DONE"}}]}
    mock_response_results = MagicMock()
    mock_response_results.json.return_value = {"results": []}

    call_count = 0

    async def post_with_pool_timeout(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise httpx.PoolTimeout("Pool exhausted")
        return mock_response_create

    with patch.object(client, "_client") as mock_client:
        mock_client.post = post_with_pool_timeout
        mock_client.get = AsyncMock(side_effect=[mock_response_status, mock_response_results])

        with patch("app.pipeline.splunk_client._async_sleep", new_callable=AsyncMock):
            results = await client.execute_spl("index=test | stats count")

    assert results == []
    assert call_count == 3  # failed twice, succeeded on third


@pytest.mark.asyncio
async def test_execute_spl_empty_results(client):
    """Query returning zero results."""
    mock_response_create = MagicMock()
    mock_response_create.json.return_value = {"sid": "789"}
    mock_response_status = MagicMock()
    mock_response_status.json.return_value = {"entry": [{"content": {"dispatchState": "DONE"}}]}
    mock_response_results = MagicMock()
    mock_response_results.json.return_value = {"results": []}

    with patch.object(client, "_client") as mock_client:
        mock_client.post = AsyncMock(return_value=mock_response_create)
        mock_client.get = AsyncMock(side_effect=[mock_response_status, mock_response_results])

        results = await client.execute_spl("index=test earliest=-1d | stats count")
    assert results == []


@pytest.mark.asyncio
async def test_execute_spl_backoff_times(client):
    """Verify exponential backoff: 1s, 2s."""
    sleep_times = []

    async def track_sleep(seconds):
        sleep_times.append(seconds)

    with patch.object(client, "_client") as mock_client:
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("app.pipeline.splunk_client._async_sleep", side_effect=track_sleep):
            with pytest.raises(ConnectionError):
                await client.execute_spl("index=test | stats count")

    # 3 attempts = 2 sleeps (between 1-2, between 2-3)
    assert len(sleep_times) == 2
    assert sleep_times[0] == 1.0   # BACKOFF_BASE * 2^0
    assert sleep_times[1] == 2.0   # BACKOFF_BASE * 2^1


@pytest.mark.asyncio
async def test_discover_schema(client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "entry": [
            {"name": "chocolate_index", "content": {"totalEventCount": "1000"}}
        ]
    }

    with patch.object(client, "_client") as mock_client:
        mock_client.get = AsyncMock(return_value=mock_response)
        schema = await client.discover_schema()
        assert "indexes" in schema


@pytest.mark.asyncio
async def test_discover_schema_skips_internal_indexes(client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "entry": [
            {"name": "_internal", "content": {}},
            {"name": "_audit", "content": {}},
            {"name": "user_index", "content": {}},
        ]
    }

    with patch.object(client, "_client") as mock_client:
        mock_client.get = AsyncMock(return_value=mock_response)
        schema = await client.discover_schema()
        assert "_internal" not in schema["indexes"]
        assert "_audit" not in schema["indexes"]
        assert "user_index" in schema["indexes"]


@pytest.mark.asyncio
async def test_discover_schema_connection_error(client):
    with patch.object(client, "_client") as mock_client:
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ConnectionError, match="Cannot reach Splunk"):
            await client.discover_schema()
