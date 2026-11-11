"use client";
import {useSyncExternalStore} from 'react';

const subscribe=()=>()=>{};
const clientSnapshot=()=>true;
const serverSnapshot=()=>false;

/** Mount preference-driven controls only after hydration, with one initial state. */
export function useClientReady(){
 return useSyncExternalStore(subscribe,clientSnapshot,serverSnapshot);
}
