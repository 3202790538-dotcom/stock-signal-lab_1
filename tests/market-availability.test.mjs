import assert from 'node:assert/strict';
import test from 'node:test';
import {gateStudySignals} from '../app/market-availability.ts';

test('running or failed refresh suppresses displayed candidates while preserving historical trades',()=>{
 const study={simulation:{state:{label:'正常'},latestSignals:[{symbol:'NVDA',status:'candidate'}],trades:[{symbol:'MU'}]},marketSnapshotStatus:{signalsAllowed:true,reason:null}};
 for(const reason of ['行情正在同步','刷新失败']){
  const guarded=gateStudySignals(study,reason);
  assert.deepEqual(guarded.simulation.latestSignals,[]);
  assert.equal(guarded.marketSnapshotStatus.signalsAllowed,false);
  assert.equal(guarded.marketSnapshotStatus.reason,reason);
  assert.deepEqual(guarded.simulation.trades,[{symbol:'MU'}]);
 }
 assert.equal(study.simulation.latestSignals.length,1);
 assert.equal(gateStudySignals(study,''),study);
});
