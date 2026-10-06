"""Bounded public-web fetches, with DNS checked at connection time."""
import asyncio
import ipaddress
import socket
import ssl
from urllib.parse import urlparse, urljoin

import aiohttp
import certifi
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only public HTTPS URLs without credentials are accepted.')
    if parsed.port not in (None, 443):
        raise ValueError('Nonstandard ports are not accepted.')
    if parsed.hostname.lower() == 'localhost' or parsed.hostname.lower().endswith(('.local', '.localhost')):
        raise ValueError('Local addresses are not accepted.')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise ValueError('Private or special addresses are not accepted.')


class PublicResolver(AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not infos or any(not ipaddress.ip_address(info[4][0]).is_global or ipaddress.ip_address(info[4][0]).is_multicast or ipaddress.ip_address(info[4][0]).is_reserved for info in infos):
            raise ValueError('DNS resolved to a private or special address.')
        return [dict(hostname=host, host=info[4][0], port=port, family=info[0],
                     proto=info[2], flags=socket.AI_NUMERICHOST) for info in infos]

    async def close(self):
        pass


async def fetch_text(url: str) -> tuple[str, str]:
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False,
                                    ssl=ssl.create_default_context(cafile=certifi.where()))
    async with aiohttp.ClientSession(connector=connector, trust_env=False,
                                    timeout=aiohttp.ClientTimeout(total=12)) as client:
        for _ in range(4):
            validate_url(url)
            async with client.get(url, allow_redirects=False, headers={'User-Agent': 'ReelFactChecker/0.2'}) as response:
                if response.status in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                response.raise_for_status()
                if not any(t in response.headers.get('Content-Type', '') for t in ('text/html', 'text/plain')):
                    raise ValueError('Only HTML and plain text sources are supported.')
                body = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    body.extend(chunk)
                    if len(body) > 1_000_000:
                        raise ValueError('Source exceeds the one megabyte limit.')
                soup = BeautifulSoup(body.decode('utf-8', errors='replace'), 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript']):
                    tag.decompose()
                text = ' '.join(soup.get_text(' ', strip=True).split())[:18000]
                if len(text) < 80:
                    raise ValueError('Source has too little readable text.')
                return url, text
    raise ValueError('Too many redirects.')
