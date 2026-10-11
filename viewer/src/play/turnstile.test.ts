// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest';
import {getTurnstileToken} from './turnstile';
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();});
it('disables an expired token and accepts the refreshed widget token',async()=>{let callbacks:Record<string,unknown>={};vi.stubGlobal('turnstile',{render:(_el:unknown,opts:Record<string,unknown>)=>{callbacks=opts;return 'widget';},remove:vi.fn()});const changed=vi.fn();const check=getTurnstileToken(document.createElement('div'),'site',changed);await Promise.resolve();expect(callbacks.appearance).toBe('interaction-only');expect(callbacks.execution).toBeUndefined();(callbacks.callback as (t:string)=>void)('first');expect(await check.token).toBe('first');(callbacks['expired-callback'] as ()=>void)();expect(changed).toHaveBeenLastCalledWith(null);(callbacks.callback as (t:string)=>void)('refreshed');expect(changed).toHaveBeenLastCalledWith('refreshed');check.cancel();});
