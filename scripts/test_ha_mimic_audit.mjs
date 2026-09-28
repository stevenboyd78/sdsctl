import assert from 'node:assert/strict';
import test from 'node:test';
import {checkGeometry, fixturePage, parseArguments} from './audit_home_assistant_mimic.mjs';

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
  const page=fixturePage({example:'</script><script>unexpected()</script>'});
  assert.ok(!page.includes('<script>unexpected()'));
  assert.ok(page.includes('\\u003c/script>'));
  assert.ok(page.includes("window.fetch=async"));
});

for (const [name, change] of Object.entries({
  zero_width:{width:0}, zero_height:{height:0}, pixel_frame:{border:12,edges:[12,12,12,12]},
  unequal_edges:{edges:[10,10,9,10]}, wrong_treatment:{right:'rgba(0, 0, 0, 0)'},
  outside_cells:{outside:1}, outside_grid:{gridFits:false}, overlaps:{overlap:1}, horizontal_overflow:{overflow:true},
  wrong_columns:{columns:29}, wrong_rows:{rows:21},
})) {
  test(`refuses ${name}`, () => assert.throws(()=>checkGeometry({...panel,...change}, 'border')));
}
