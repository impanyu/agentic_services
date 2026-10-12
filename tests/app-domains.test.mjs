import {test} from 'node:test';
import assert from 'node:assert/strict';
import {publicRequestURL,canonicalDiscovery,browserOrigins,allowedBrowserOrigin} from '../gateway/src/app-domains.ts';

test('canonical paid resource URLs keep request query and legacy requests remain unchanged',()=>{
 assert.equal(publicRequestURL('http://gateway/photo-scout/v1/discover?x=1','photo-scout.aisoup.net','https://api.aisoup.net').href,'https://photo-scout.aisoup.net/api/v1/discover?x=1');
 assert.equal(publicRequestURL('http://gateway/v1/claims/verify','web-evidence.aisoup.net','https://api.aisoup.net').href,'https://web-evidence.aisoup.net/api/v1/claims/verify');
 assert.equal(publicRequestURL('http://gateway/photo-scout/v1/discover','api.aisoup.net','https://api.aisoup.net').href,'https://api.aisoup.net/photo-scout/v1/discover');
 assert.equal(publicRequestURL('http://gateway/photo-scout/v1/discover','evil.test','https://api.aisoup.net').hostname,'api.aisoup.net');
 assert.ok(browserOrigins.has('https://photo-scout.aisoup.net'));assert.ok(!browserOrigins.has('https://evil.aisoup.net'));
 assert.ok(allowedBrowserOrigin('https://photo-scout.aisoup.net','photo-scout'));assert.ok(!allowedBrowserOrigin('https://contractor-check.aisoup.net','photo-scout'));
});
test('canonical docs strip only their service prefix and do not mutate legacy documents',()=>{
 const original={servers:[{url:'https://api.aisoup.net'}],paths:{'/photo-scout/v1/discover':{},'/contractor-check/v1/check':{}},remotes:[{url:'https://api.aisoup.net/photo-scout/mcp'}],websiteUrl:'https://aisoup.net/photo-scout/'};
 const rewritten=canonicalDiscovery(original,'photo-scout');
 assert.deepEqual(Object.keys(rewritten.paths),['/v1/discover']);assert.equal(rewritten.servers[0].url,'https://photo-scout.aisoup.net/api');
 assert.equal(rewritten.remotes[0].url,'https://photo-scout.aisoup.net/api/mcp');assert.equal(rewritten.websiteUrl,'https://photo-scout.aisoup.net/');
 assert.equal(original.servers[0].url,'https://api.aisoup.net');assert.ok('/photo-scout/v1/discover' in original.paths);
});
