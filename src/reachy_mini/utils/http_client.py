"""HTTP client utilities with retry mechanism for Reachy Mini.

This module provides HTTP client utilities with automatic retry logic
to handle temporary network failures and improve robustness.
"""

import asyncio
import logging
from typing import Any, Optional

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None


logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for HTTP retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 10.0,
        exponential_backoff: bool = True,
        retryable_statuses: Optional[set[int]] = None,
        retryable_exceptions: Optional[set[type[Exception]]] = None,
    ):
        """Initialize retry configuration.

        Args:
            max_attempts: Maximum number of retry attempts (default: 3)
            base_delay: Base delay between retries in seconds (default: 1.0)
            max_delay: Maximum delay between retries in seconds (default: 10.0)
            exponential_backoff: Use exponential backoff for delays (default: True)
            retryable_statuses: HTTP status codes that trigger retry (default: {408, 429, 500, 502, 503, 504})
            retryable_exceptions: Exception types that trigger retry (default: None)
        """
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_backoff = exponential_backoff
        self.retryable_statuses = retryable_statuses or {408, 429, 500, 502, 503, 504}
        self.retryable_exceptions = retryable_exceptions


class HttpClient:
    """HTTP client with automatic retry mechanism."""

    def __init__(self, timeout: float = 30.0, retry_config: Optional[RetryConfig] = None):
        """Initialize HTTP client.

        Args:
            timeout: Request timeout in seconds (default: 30.0)
            retry_config: Retry configuration (default: default RetryConfig)
        """
        if not HTTPX_AVAILABLE:
            logger.warning("httpx not available, using basic HTTP client without retry")
            self._use_httpx = False
        else:
            self._use_httpx = True
            self._client = httpx.AsyncClient(timeout=timeout)

        self.retry_config = retry_config or RetryConfig()
        self.logger = logging.getLogger(f"{__name__}.HttpClient")

    async def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Perform GET request with retry logic.

        Args:
            url: URL to request
            params: Query parameters
            headers: Request headers

        Returns:
            JSON response as dictionary

        Raises:
            Exception: If all retry attempts fail
        """
        if self._use_httpx:
            return await self._get_with_retry(url, params=params, headers=headers)
        else:
            # Fallback for when httpx is not available
            return await self._get_basic(url, params=params, headers=headers)

    async def _get_with_retry(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Perform GET request with httpx and retry logic."""
        last_exception = None

        for attempt in range(1, self.retry_config.max_attempts + 1):
            try:
                response = await self._client.get(url, params=params, headers=headers)
                response.raise_for_status()

                if response.status_code in self.retry_config.retryable_statuses:
                    self.logger.warning(
                        f"Request to {url} returned status {response.status_code}, "
                        f"retrying (attempt {attempt}/{self.retry_config.max_attempts})"
                    )
                    await self._wait_before_retry(attempt)
                    continue

                return response.json()

            except httpx.HTTPStatusError as e:
                last_exception = e
                if e.response.status_code in self.retry_config.retryable_statuses:
                    self.logger.warning(
                        f"HTTP error {e.response.status_code} for {url}, "
                        f"retrying (attempt {attempt}/{self.retry_config.max_attempts})"
                    )
                    await self._wait_before_retry(attempt)
                else:
                    raise

            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_exception = e
                self.logger.warning(
                    f"Network error for {url}: {type(e).__name__}, "
                    f"retrying (attempt {attempt}/{self.retry_config.max_attempts})"
                )
                await self._wait_before_retry(attempt)

            except Exception as e:
                if self.retry_config.retryable_exceptions and isinstance(e, self.retry_config.retryable_exceptions):
                    last_exception = e
                    self.logger.warning(
                        f"Exception for {url}: {type(e).__name__}, "
                        f"retrying (attempt {attempt}/{self.retry_config.max_attempts})"
                    )
                    await self._wait_before_retry(attempt)
                else:
                    raise

        # All retries exhausted
        self.logger.error(f"All retry attempts failed for {url}")
        raise last_exception or Exception("All retry attempts failed")

    async def _get_basic(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Basic GET request without retry (fallback)."""
        # This is a minimal fallback implementation
        # In production, you'd want to use a proper HTTP library
        import urllib.parse
        import urllib.request
        import json

        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode())
        except Exception as e:
            self.logger.error(f"Basic HTTP request failed for {url}: {e}")
            raise

    async def _wait_before_retry(self, attempt: int) -> None:
        """Wait before retry based on retry configuration."""
        if self.retry_config.exponential_backoff:
            delay = min(
                self.retry_config.base_delay * (2 ** (attempt - 1)),
                self.retry_config.max_delay,
            )
        else:
            delay = min(
                self.retry_config.base_delay * attempt,
                self.retry_config.max_delay,
            )

        self.logger.debug(f"Waiting {delay:.2f}s before retry")
        await asyncio.sleep(delay)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._use_httpx and self._client:
            await self._client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


async def get_with_retry(
    url: str,
    params: Optional[dict[str, Any]] = None,
    headers: Optional[dict[str, str]] = None,
    max_attempts: int = 3,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Convenience function to perform GET request with retry.

    Args:
        url: URL to request
        params: Query parameters
        headers: Request headers
        max_attempts: Maximum number of retry attempts
        timeout: Request timeout in seconds

    Returns:
        JSON response as dictionary

    Raises:
        Exception: If all retry attempts fail

    Example:
        >>> data = await get_with_retry(
        ...     "http://localhost:8000/api/volume/current",
        ...     max_attempts=3,
        ...     timeout=5.0
        ... )
    """
    retry_config = RetryConfig(max_attempts=max_attempts)
    async with HttpClient(timeout=timeout, retry_config=retry_config) as client:
        return await client.get(url, params=params, headers=headers)