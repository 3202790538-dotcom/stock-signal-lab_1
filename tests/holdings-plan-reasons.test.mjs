import assert from 'node:assert/strict';
import test from 'node:test';
import * as helpers from '../app/holdings-plan-types.ts';

const item = (kind, status, issues = []) => ({
  decision: {kind, label: '现有判断', summary: '现有解释', timing: '收盘后确认'},
  dataStatus: {kind: status, label: '数据状态', bars: 240, marketAsOf: '2026-11-10', accountFresh: true, marketFresh: true, technicalEligible: true, issues},
});
const quoteIssue = {code: 'position_quote_missing', label: '缺少个股行情', action: '刷新行情后重算计划'};

test('a blocked position uses its concrete missing input and remedy', () => {
  assert.equal(typeof helpers.getHoldingsRowState, 'function');
  const state = helpers.getHoldingsRowState(item('verify', 'verify', [quoteIssue]));
  assert.equal(state.blocked, true);
  assert.equal(state.label, '缺个股行情');
  assert.equal(state.summary, '刷新行情；仍缺失时展开检查。');
  assert.equal(state.issue, quoteIssue);
});

test('a live refresh or expiry supersedes previously valid exit conditions', () => {
  assert.equal(typeof helpers.getHoldingsRowState, 'function');
  const expiry = {code: 'account_stale', label: '账户快照已过时', action: '人工同步 IBKR 后重算计划'};
  const state = helpers.getHoldingsRowState(item('review_exit', 'ready'), expiry);
  assert.equal(state.blocked, true);
  assert.equal(state.kind, 'verify');
  assert.equal(state.label, '账户快照已过期');
  assert.equal(state.summary, '先处理上方数据提示，再刷新计划。');
  assert.equal(state.issue, expiry);
});

test('risk-only limitations do not hide a valid concentration reduction or exit review', () => {
  assert.equal(typeof helpers.getHoldingsRowState, 'function');
  const limitations = [{code: 'etf_risk_only', label: 'ETF 仅作账户风险复核', action: '核对账户限制'}];
  const reduction = helpers.getHoldingsRowState(item('review_reduce', 'risk_only', limitations));
  assert.equal(reduction.blocked, false);
  assert.equal(reduction.kind, 'review_reduce');
  const exit = helpers.getHoldingsRowState(item('review_exit', 'ready'));
  assert.equal(exit.blocked, false);
  assert.equal(exit.label, '现有判断');
});

test('insufficient history with a verify decision still names the limitation', () => {
  assert.equal(typeof helpers.getHoldingsRowState, 'function');
  const issue = {code: 'history_insufficient', label: '日线历史不足', action: '等待历史累积，先检查账户限额'};
  const state = helpers.getHoldingsRowState(item('verify', 'risk_only', [issue]));
  assert.equal(state.blocked, true);
  assert.equal(state.label, '日线样本不足');
  assert.equal(state.issue, issue);
});

test('long global explanations stay intact in details while rows show a short remedy', () => {
  const issue = {code: 'latest_bar_missing', label: '基准最新完整日线未就绪，或日线日期未一致，需要继续确认', action: '刷新公开行情，并核实基准与个股最新完整交易日；节假日需另行核实，完整日线恢复前不使用候选。'};
  const state = helpers.getHoldingsRowState(item('review_exit', 'ready'), issue);
  assert.equal(state.label, '缺最新日线');
  assert.equal(state.summary, '刷新行情；完整日线恢复后重算。');
  assert.equal(state.blocked, true);
  assert.equal(state.kind, 'verify');
  assert.equal(state.issue, issue);
});

test('an unknown issue stays blocked with a usable details link and keeps the full explanation', () => {
  const issue = {code: 'new_provider_problem', label: '服务端新发现的问题需要人工核对完整详情', action: '请检查新提供的数据源，然后处理服务端描述的异常。'};
  const state = helpers.getHoldingsRowState(item('verify', 'verify', [issue]));
  assert.equal(state.blocked, true);
  assert.equal(state.label, '数据需确认');
  assert.equal(state.summary, '展开本项，查看完整原因与处理方法。');
  assert.equal(state.issue, issue);
});

test('plan preferences survive account-tab remount without accepting malformed saved rules', () => {
  assert.equal(typeof helpers.parseHoldingsPlanPreferences, 'function');
  const fallback = {templateId: 'breakout', riskProfile: 'aggressive', allowProfitAdd: true};
  const stored = {templateId: 'pullback', riskProfile: 'balanced', allowProfitAdd: false};
  assert.deepEqual(helpers.parseHoldingsPlanPreferences(JSON.stringify(stored), fallback), stored);
  for (const bad of [null, '{}', 'broken', JSON.stringify({...stored, riskProfile: 'maximum'}), JSON.stringify({...stored, allowProfitAdd: 'false'})]) {
    assert.deepEqual(helpers.parseHoldingsPlanPreferences(bad, fallback), fallback);
  }
});
