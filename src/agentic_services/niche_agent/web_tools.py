"""Bounded source reads; access grants and redirect scopes are resolved outside the model."""
import asyncio
import hashlib
import os
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agents import function_tool

from ..snapshot import _normalized_content, _validate_public_url
from .store import ManagerStore


def configured_domains() -> set[str]:
    if not os.getenv('NICHE_AGENT_FETCH_REFERENCE', '').strip():
        return set()
    domains = {s.strip().lower() for s in os.getenv('NICHE_AGENT_FETCH_DOMAINS', '').split(',') if s.strip()}
    # Reddit content still requires the dedicated OAuth/deletion-compliant adapter.
    return {s for s in domains if not (s == 'reddit.com' or s.endswith('.reddit.com') or s.endswith('.redd.it') or s == 'redd.it')}


def validate_scope(url: str, domains: set[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in domains or parts.port not in {None, 443}:
        raise ValueError('URL outside operator-granted HTTPS source domains')
    _validate_public_url(url)


def fetch(url: str, domains: set[str]) -> dict:
    validate_scope(url, domains)

    class RedirectScope(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate_scope(newurl, domains)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = build_opener(RedirectScope())
    req = Request(url, headers={'User-Agent':'AISoup-NicheManager/1.0 (+https://aisoup.net)',
                               'Accept':'text/html,text/plain,application/json,application/xml'})
    with opener.open(req, timeout=12) as response:
        final = response.geturl()
        validate_scope(final, domains)
        raw = response.read(262145)
        if len(raw) > 262144:
            raise ValueError('Document exceeds 256 KiB')
        normalized = _normalized_content(raw, response.headers.get_content_type())
        if normalized is None:
            raise ValueError('Unsupported document type')
        text = normalized.decode('utf-8', errors='replace')[:12000]
        return {'url': final, 'requestedUrl': url, 'sha256':hashlib.sha256(raw).hexdigest(),
                'text': text, 'status':'fetched', 'storedAsSignal':False}


def build_web_tools(store: ManagerStore, owner: str):
    # Fetch receipts are run-local; durable SDK transcripts contain the bounded read.
    # The agent can only ingest literal excerpts from a successfully fetched document.
    receipts: dict[str, dict] = {}

    @function_tool(timeout=20)
    async def read_source_document(url: str) -> dict:
        """Read HTML/text/JSON/XML from operator-approved HTTPS domains, checking redirects.
        Returns a bounded document and receipt. Blocked domains remain unavailable.
        """
        domains = configured_domains()
        if not domains:
            return {'status':'not_configured', 'approvedDomains':[]}
        try:
            result = await asyncio.to_thread(fetch, url, domains)
        except Exception as error:
            return {'status':'blocked_or_failed', 'errorType':type(error).__name__}
        receipt = 'doc_' + hashlib.sha256((result['url'] + result['sha256']).encode()).hexdigest()
        receipts[receipt] = result
        return {**result, 'receipt':receipt}

    @function_tool
    def record_document_signal(receipt: str, excerpt: str, kind: str, audience: str) -> dict:
        """Record an exact 25..500 character excerpt from a successful read receipt as evidence.
        Never invent/paraphrase source text here. Assessment synthesis happens in revise_niche.
        """
        document = receipts.get(receipt)
        if not document or not 25 <= len(excerpt) <= 500 or excerpt not in document['text']:
            raise ValueError('Exact source excerpt and valid fetch receipt required')
        if kind not in {'complaint', 'suggestion'}:
            raise ValueError('Expected complaint or suggestion')
        validate_scope(document['url'], configured_domains())
        from ..niche_discovery import now
        with store.connect() as db:
            store.assert_owner(db, owner)
        external_id = 'document:' + hashlib.sha256((document['url'] + excerpt).encode()).hexdigest()
        added = store.ingest_external_signal(external_id=external_id, origin='approved_document',
            kind=kind,text=excerpt,source_url=document['url'], observed_at=now(),audience=audience[:180])
        with store.connect() as db:
            row = db.execute('SELECT id FROM niche_signals WHERE external_id=?', (external_id,)).fetchone()
        return {'added':added, 'signalId':row[0] if row else None, 'sourceUrl':document['url']}

    return [read_source_document, record_document_signal]
