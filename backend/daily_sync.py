"""Once per 09:00 Shanghai cycle while the local service runs; no broker connection."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import json
import math
from pathlib import Path
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

from personal_account import atomic_json_write, load_settings, selected_holdings
from watchlists import load_registry, safe_quote_symbol
from security_metadata import enrich_market

SHANGHAI = ZoneInfo('Asia/Shanghai')
CHANNELS = ('market', 'company', 'news')


def _snapshot(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result if result.tzinfo else None
    except ValueError:
        return None


def _empty_channel():
    return {'status': 'idle', 'lastAttemptAt': None, 'lastSuccessAt': None,
            'lastError': None, 'updatedCount': 0}


def _state(path):
    saved = _snapshot(path)
    channels = saved.get('channels') if isinstance(saved.get('channels'), dict) else {}
    return {'lastAttemptAt': saved.get('lastAttemptAt'), 'lastSuccessAt': saved.get('lastSuccessAt'),
            'lastCycleKey': saved.get('lastCycleKey'),
            'channels': {name: {**_empty_channel(), **(channels.get(name) if isinstance(channels.get(name), dict) else {})}
                         for name in CHANNELS}}


def _stale(path, hours, now, payload_key):
    value = _snapshot(path)
    stamp = _timestamp(value.get('updatedAt'))
    age = (now-stamp).total_seconds() if stamp else None
    payload = value.get(payload_key)
    present = isinstance(payload, dict) and bool(payload) if payload_key == 'company' else isinstance(payload, list)
    return (not present or age is None or age < 0 or age >= hours*3600
            or value.get('status') in ('error', 'unconfigured', 'unavailable'))


def _research_symbols(data_dir):
    market = _snapshot(data_dir/'dashboard.json')
    holdings = selected_holdings(data_dir, market, load_settings(data_dir))
    stock_rows = market.get('stocks', [])
    stock_rows = stock_rows if isinstance(stock_rows, list) else []
    positions = holdings.get('positions', [])
    positions = positions if isinstance(positions, list) else []
    symbols = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        symbol, quantity = row.get('contract_description'), row.get('position')
        if (not safe_quote_symbol(symbol) or isinstance(quantity, bool)
                or not isinstance(quantity, (int, float)) or not math.isfinite(quantity)
                or quantity == 0 or row.get('currency') != 'USD' or row.get('asset_class') != 'STK'):
            continue
        symbols.append(symbol)
    symbols.extend(row['quoteSymbol'] for row in load_registry(data_dir)['instruments'] if row['supported'])
    verified = []
    for symbol in dict.fromkeys(symbols):
        matches = [s for s in stock_rows if isinstance(s, dict) and s.get('symbol') == symbol]
        if len(matches) == 1 and matches[0].get('quoteType') == 'EQUITY' and matches[0].get('currency') == 'USD':
            verified.append(symbol)
    return verified


def fund_research_exclusion(stock):
    """Recognize sourced fund identities, not every exchange-listed equity.

    Yahoo labels some listed funds EQUITY. The issuer's DXYZ page identifies
    its closed-end structure; require its name and currency as well as ticker
    so the exception cannot silently follow a reused symbol.
    """
    if not isinstance(stock, dict):
        return None
    evidence = stock.get('metadataEvidence')
    if (stock.get('securityKind') == 'fund' and isinstance(evidence, dict)
            and isinstance(evidence.get('url'), str) and evidence['url'].startswith('https://')):
        return {'sourceUrl': evidence['url'], 'sourceLabel': evidence.get('source') or '证券类型来源',
                'lastError': '已核实为基金产品，不适用普通公司的季度营收、利润与估值比较；请查看基金净值、持仓和发行人披露。'}
    compact_name = ''.join(c for c in str(stock.get('name', '')).casefold() if c.isalnum())
    if (stock.get('symbol') == 'DXYZ' and stock.get('currency') == 'USD'
            and compact_name in ('destinytech100inc', 'destinytech100')):
        return {'sourceUrl': 'https://destiny.xyz/tech100', 'sourceLabel': 'Destiny Tech100 发行人说明',
                'lastError': '已核实 DXYZ 是封闭式基金，不适用普通公司的季度营收、利润与估值比较；请查看发行人披露的基金净值和投资组合。'}
    return None


class DailySync:
    """Callbacks perform one refresh and raise (or return an error status) on failure.

    Construction/status is read-only. Only start() runs a background thread.
    Existing provider locks remain the callbacks' responsibility. The scheduler
    persists the attempt before any work, so a failed/restarted cycle is not retried.
    A pre-09:00 startup catches up the preceding cycle; today's 09:00 remains due.
    """
    def __init__(self, data_dir, refresh_market, refresh_company, refresh_news, *, clock=None, interval_seconds=30):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir/'daily-sync.json'
        self.callbacks = {'market': refresh_market, 'company': refresh_company, 'news': refresh_news}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.interval_seconds = interval_seconds
        self._state = _state(self.path)
        # A prior process may have exited during a channel update. Preserve its
        # attempt/success dates without claiming that it is still running.
        for channel in self._state['channels'].values():
            if channel['status'] == 'running':
                channel.update(status='error', lastError='上次更新在服务停止时中断；保留此前缓存。')
        self._state_lock, self._run_lock = Lock(), Lock()
        self._stop = Event()
        self._thread = None
        self._running = False

    def _now(self, value=None):
        result = value if value is not None else self.clock()
        if not isinstance(result, datetime) or result.tzinfo is None:
            raise ValueError('同步时间必须包含时区')
        return result.astimezone(timezone.utc)

    def status(self, *, now=None):
        current = self._now(now).astimezone(SHANGHAI)
        with self._state_lock:
            result = deepcopy(self._state)
            result['running'] = self._running
        scheduled = current.replace(hour=9, minute=0, second=0, microsecond=0)
        if current >= scheduled:
            scheduled += timedelta(days=1)
        result['nextRunAt'] = scheduled.isoformat()
        return result

    def _save(self):
        with self._state_lock:
            value = deepcopy(self._state)
        atomic_json_write(self.path, value)

    def _set_channel(self, name, **values):
        with self._state_lock:
            self._state['channels'][name].update(values)
        self._save()

    @staticmethod
    def _invoke(callback, *args):
        result = callback(*args)
        if isinstance(result, dict) and (result.get('ok') is False or
                result.get('status') in ('error', 'unavailable', 'unconfigured')):
            raise RuntimeError('数据源未完成更新')

    def _market(self, stamp):
        self._set_channel('market', status='running', lastAttemptAt=stamp, lastError=None, updatedCount=0)
        try:
            self._invoke(self.callbacks['market'])
        except Exception:
            self._set_channel('market', status='error', lastError='日线更新失败，保留上次有效行情。')
            return False
        market = _snapshot(self.data_dir/'dashboard.json')
        refresh = market.get('quoteRefresh') or {}
        # The provider can successfully return a series whose latest completed
        # bar is missing OHLC fields. Its retained older bars remain useful, but
        # this is a partial update, not a new fully successful daily snapshot.
        errors = [*(refresh.get('errors') or []), *(market.get('marketWarnings') or [])]
        count = len(refresh.get('updatedSymbols') or [])
        if errors:
            symbols = list(dict.fromkeys(str(row.get('symbol', '未知代码')) for row in errors if isinstance(row, dict)))
            detail = '、'.join(symbols[:5])+('等' if len(symbols) > 5 else '')
            self._set_channel('market', status='error', updatedCount=count,
                              lastError=f'{detail} 日线未完整更新或存在字段缺项（共 {len(errors)} 条），保留有效缓存。')
            return False
        self._set_channel('market', status='success', lastSuccessAt=stamp, updatedCount=count)
        return True

    def _research(self, name, symbols, current):
        stamp = current.isoformat()
        hours, payload = (12, 'company') if name == 'company' else (.25, 'items')
        prefix = 'company' if name == 'company' else 'news'
        if name == 'company':
            stocks = enrich_market(_snapshot(self.data_dir/'dashboard.json'), self.data_dir).get('stocks', [])
            excluded = {row.get('symbol') for row in stocks if fund_research_exclusion(row)}
            symbols = [symbol for symbol in symbols if symbol not in excluded]
        due = [s for s in symbols if _stale(self.data_dir/f'{prefix}-{s}.json', hours, current, payload)]
        if not due:
            self._set_channel(name, status='skipped', lastAttemptAt=stamp, lastError=None, updatedCount=0)
            return True
        self._set_channel(name, status='running', lastAttemptAt=stamp, lastError=None, updatedCount=0)
        failed, updated = [], 0
        for symbol in due:
            try:
                self._invoke(self.callbacks[name], symbol)
                updated += 1
            except Exception:
                failed.append(symbol)
        if failed:
            label = '财报' if name == 'company' else '新闻'
            detail = '、'.join(failed[:5])+('等' if len(failed) > 5 else '')
            self._set_channel(name, status='error', updatedCount=updated,
                              lastError=f'{detail} {label}更新失败（共 {len(failed)} 项），保留有效缓存。')
            return False
        self._set_channel(name, status='success', lastSuccessAt=stamp, updatedCount=updated)
        return True

    def run_due(self, *, now=None, startup=False):
        current = self._now(now)
        local = current.astimezone(SHANGHAI)
        if not startup and local.hour < 9:
            return False
        if not self._run_lock.acquire(blocking=False):
            return False
        lock_file = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            lock_file = (self.data_dir/'.daily-sync.lock').open('a')
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            saved = _state(self.path)
            previous = _timestamp(saved['lastAttemptAt'])
            previous_cycle = saved['lastCycleKey'] or (
                (previous.astimezone(SHANGHAI)-timedelta(hours=9)).date().isoformat() if previous else None)
            cycle = (local-timedelta(hours=9)).date().isoformat()
            if isinstance(previous_cycle, str) and previous_cycle >= cycle:
                return False
            stamp = current.isoformat()
            with self._state_lock:
                self._state = saved
                self._state['lastAttemptAt'] = stamp
                self._state['lastCycleKey'] = cycle
                self._running = True
            self._save()
            successful = self._market(stamp)
            try:
                symbols = _research_symbols(self.data_dir)
            except (ValueError, TypeError, OSError):
                symbols = None
            for name in ('company', 'news'):
                if symbols is None:
                    self._set_channel(name, status='error', lastAttemptAt=stamp, updatedCount=0,
                                      lastError='账户来源无法读取，本次未更新持仓资料；请核对账户设置。')
                    successful = False
                elif not self._research(name, symbols, current):
                    successful = False
            if successful:
                with self._state_lock:
                    self._state['lastSuccessAt'] = stamp
                self._save()
            return True
        finally:
            with self._state_lock:
                self._running = False
            if lock_file is not None:
                lock_file.close()
            self._run_lock.release()

    def _loop(self):
        startup = True
        while not self._stop.is_set():
            try:
                self.run_due(startup=startup)
            except (OSError, ValueError):
                # A storage failure cannot safely persist an attempt; leave this
                # process stopped instead of repeatedly retrying provider calls.
                return
            startup = False
            if self._stop.wait(self.interval_seconds):
                return

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._loop, name='northstar-daily-sync', daemon=True)
        self._thread.start()

    def stop(self, timeout=1):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
