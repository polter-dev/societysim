import {describe,expect,it} from 'vitest';
import {publishedBase} from './staticSource';
describe('publishedBase',()=>{
 it('defaults to the same origin and deployment directory',()=>expect(publishedBase('?game=one','https://society-sim.us/').href).toBe('https://society-sim.us/'));
 it('accepts an override and normalizes its directory',()=>expect(publishedBase('?base=https%3A%2F%2Ftest.example%2Fbatch','http://localhost:3012/').href).toBe('https://test.example/batch/'));
 it('rejects local filesystem and non-http URLs',()=>expect(()=>publishedBase('?base=file:///C:/logs/','http://localhost:3012/')).toThrow('HTTP'));
});
