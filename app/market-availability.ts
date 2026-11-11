import type {Study} from './complete-strategy-types';

/** Local refresh state can invalidate a previously delivered study immediately. */
export function gateStudySignals(study:Study,reason:string):Study{
 if(!reason)return study;
 return {...study,marketSnapshotStatus:{signalsAllowed:false,reason},simulation:{...study.simulation,
  latestSignals:[],state:{...study.simulation.state,dataStatus:'stale',label:'行情未就绪，仅保留截至历史日期的模拟记录'}}};
}
