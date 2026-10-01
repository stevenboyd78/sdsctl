import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import test from 'node:test';
import {checkGeometry, fixturePage, parseArguments, validateResourceBundle} from './audit_home_assistant_mimic.mjs';

function resourceBundle() {
  const make = (name, body) => ({url:`/local/sds200/${name}.js?v=${createHash('sha256').update(body).digest('hex')}`,body});
  const resources = ['sds200-card','sds200-display-card','sds200-waterfall-card','sds200-mimic-card']
    .map(element=>({...make(element, `// ${element}\n`),element}));
  return {resources,aggregate:make('sds200-cards', resources.map(resource=>`import "${resource.url}";\n`).join(''))};
}

test('exact digest-qualified four-card bundle is accepted without rewriting bytes', () => {
  const bundle = resourceBundle();
  assert.equal(validateResourceBundle(bundle), bundle);
  const page = fixturePage({}, bundle.aggregate.url);
  assert.ok(page.includes(`src="${bundle.aggregate.url}"`));
  assert.ok(!page.includes('src="/mimic.js"'));
  assert.ok(!page.includes('src="/waterfall.js"'));
});

for (const [name, mutate] of Object.entries({
  changed_bytes: bundle=>{bundle.resources[0].body+='changed';},
  stale_digest: bundle=>{bundle.resources[0].url=bundle.resources[0].url.replace(/v=.*/, 'v='+'0'.repeat(64));},
  external_origin: bundle=>{bundle.resources[0].url='https://example.invalid'+bundle.resources[0].url;},
  extra_query: bundle=>{bundle.resources[0].url+='&other=1';},
  missing_module: bundle=>{bundle.resources.pop();},
  duplicate_module: bundle=>{bundle.resources[3]=bundle.resources[0];},
  wrong_order: bundle=>{bundle.resources.reverse();},
  wrong_registration: bundle=>{bundle.resources[0].element='other-card';},
  changed_aggregate: bundle=>{bundle.aggregate.body+='// changed';},
  stale_aggregate: bundle=>{bundle.aggregate.url=bundle.aggregate.url.replace(/v=.*/, 'v='+'0'.repeat(64));},
  extra_import: bundle=>{
    bundle.aggregate.body+='import "/unexpected.js";\n';
    bundle.aggregate.url='/local/sds200/sds200-cards.js?v='+createHash('sha256').update(bundle.aggregate.body).digest('hex');
  },
})) {
  test(`packaged resource contract refuses ${name}`, () => {
    const bundle=resourceBundle();mutate(bundle);assert.throws(()=>validateResourceBundle(bundle));
  });
}

test('aggregate fixture refuses unqualified URLs and HTML attribute injection', () => {
  for (const url of ['https://example.invalid/sds200-cards.js','/local/sds200/sds200-cards.js',
    '/local/sds200/sds200-cards.js?v='+ '0'.repeat(64)+'" onload="unexpected()']) {
    assert.throws(()=>fixturePage({},url));
  }
});

const panel = Object.freeze({
  width: 600, height: 360, border: 10, edges: [10, 10, 10, 10],
  top: 'rgb(255, 225, 50)', right: 'rgb(255, 225, 50)',
  outside: 0, gridFits: true, overlap: 0, overflow: false, cells: 30, columns: 30, rows: 20,
});

test('only bounded standalone audit options are accepted', () => {
  assert.deepEqual(parseArguments([]), {chrome:null,python:null,timeoutMs:30000,help:false});
  assert.equal(parseArguments(['--help']).help, true);
  assert.deepEqual(parseArguments(['--chrome','browser','--python','python','--timeout-ms','1000']),
    {chrome:'browser',python:'python',timeoutMs:1000,help:false});
  for (const args of [['--host','scanner'],['--chrome'],['--python','--help'],
    ['--timeout-ms','0'],['--timeout-ms','Infinity'],['--timeout-ms','1.5'],['--timeout-ms','120001']]) {
    assert.throws(()=>parseArguments(args));
  }
});

test('accepts device-pixel rounding and either LED treatment without geometry changes', () => {
  checkGeometry(panel, 'border');
  checkGeometry({...panel,right:'rgba(0, 0, 0, 0)'}, 'strips');
  checkGeometry({...panel,border:10.5,edges:[10.5,10.5,10.5,10.5]}, 'border');
  // Waiting uses the same sized container, but deliberately not a CSS grid.
  checkGeometry({...panel,cells:0,columns:2,rows:2}, 'border');
});

test('fixture embeds data without allowing script tags to terminate the JSON', () => {
  const page=fixturePage({example:'</script><script>unexpected()</script>'},null,
    {keys:[{label:'</script><script>unexpected()</script>'}]});
  assert.ok(!page.includes('<script>unexpected()'));
  assert.ok(page.includes('\\u003c/script>'));
  assert.ok(page.includes("window.fetch=async"));
  assert.ok(page.includes('api/v1/scanner/front-panel'));
});

for (const [name, change] of Object.entries({
  zero_width:{width:0}, zero_height:{height:0}, pixel_frame:{border:12,edges:[12,12,12,12]},
  unequal_edges:{edges:[10,10,9,10]}, wrong_treatment:{right:'rgba(0, 0, 0, 0)'},
  outside_cells:{outside:1}, outside_grid:{gridFits:false}, overlaps:{overlap:1}, horizontal_overflow:{overflow:true},
  wrong_columns:{columns:29}, wrong_rows:{rows:21},
})) {
  test(`refuses ${name}`, () => assert.throws(()=>checkGeometry({...panel,...change}, 'border')));
}
