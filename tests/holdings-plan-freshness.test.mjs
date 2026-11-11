import assert from 'node:assert/strict';
import test from 'node:test';
import * as holdings from '../app/holdings-plan-types.ts';

const account = '2026-11-10T13:00:00Z';
const market = '2026-11-10T14:00:00Z';

test('an open plan loses account authorization immediately after its 24 hour boundary', () => {
  assert.equal(typeof holdings.getHoldingsFreshness, 'function', 'the plan needs a live freshness guard');
  const before = holdings.getHoldingsFreshness(account, market, Date.parse('2026-11-11T12:59:59.999Z'));
  assert.equal(before.accountFresh, true);
  assert.equal(before.marketFresh, true);
  assert.equal(before.nextExpiry, Date.parse('2026-11-11T13:00:00.001Z'));
  assert.equal(holdings.getHoldingsFreshness(account, market, Date.parse('2026-11-11T13:00:00Z')).accountFresh, true);
  const after = holdings.getHoldingsFreshness(account, market, Date.parse('2026-11-11T13:00:00.001Z'));
  assert.equal(after.accountFresh, false);
  assert.equal(after.marketFresh, true);
  assert.equal(after.nextExpiry, Date.parse('2026-11-11T14:00:00.001Z'));
});

test('market expiry blocks candidates independently of a fresh account', () => {
  assert.equal(typeof holdings.getHoldingsFreshness, 'function');
  const state = holdings.getHoldingsFreshness(market, account, Date.parse('2026-11-11T13:30:00Z'));
  assert.equal(state.accountFresh, true);
  assert.equal(state.marketFresh, false);
  const expired = holdings.getHoldingsFreshness(account, market, Date.parse('2026-11-11T15:00:00Z'));
  assert.equal(expired.accountFresh, false);
  assert.equal(expired.marketFresh, false);
  assert.equal(expired.nextExpiry, null);
});

test('missing, malformed, future and timezone-free timestamps cannot authorize a plan', () => {
  assert.equal(typeof holdings.getHoldingsFreshness, 'function');
  for (const stamp of ['', null, 'not a timestamp', '2026-11-12T13:00:00Z', '2026-11-10', '2026-11-10T13:00:00']) {
    const state = holdings.getHoldingsFreshness(stamp, stamp, Date.parse('2026-11-10T15:00:00Z'));
    assert.equal(state.accountFresh, false, String(stamp));
    assert.equal(state.marketFresh, false, String(stamp));
    assert.equal(state.nextExpiry, null, String(stamp));
  }
});
