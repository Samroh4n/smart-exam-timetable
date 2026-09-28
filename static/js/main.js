/* Confirm dialogs, conflict-graph drawing and comparison charts. No storage APIs used. */
document.addEventListener('submit', function (e) {
  var msg = e.target.getAttribute && e.target.getAttribute('data-confirm');
  if (msg && !window.confirm(msg)) e.preventDefault();
});

var SLOT_COLORS = ['#1d5a8a', '#d1495b', '#2a9d8f', '#e9a23b', '#7b5ea7', '#6a994e',
                   '#e07a5f', '#3d5a80', '#b5838d', '#8d6e3f', '#0f8b8d', '#7a8b99'];
var SVGNS = 'http://www.w3.org/2000/svg';

function svgEl(name, attrs, parent) {
  var el = document.createElementNS(SVGNS, name);
  for (var k in attrs) el.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(el);
  return el;
}

/* Fruchterman-Reingold style layout, computed once in the browser. */
function layout(nodes, edges, w, h) {
  var n = nodes.length, i, j, it;
  var idx = {};
  nodes.forEach(function (nd, k) {
    idx[nd.id] = k;
    var a = 2 * Math.PI * k / Math.max(n, 1);
    nd.x = w / 2 + Math.cos(a) * w / 3; nd.y = h / 2 + Math.sin(a) * h / 3;
  });
  var area = w * h, kf = Math.sqrt(area / Math.max(n, 1)) * 0.75, t = w / 8;
  for (it = 0; it < 300; it++) {
    nodes.forEach(function (nd) { nd.dx = 0; nd.dy = 0; });
    for (i = 0; i < n; i++) for (j = i + 1; j < n; j++) {
      var dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
      var d = Math.sqrt(dx * dx + dy * dy) || 0.01, f = kf * kf / d;
      nodes[i].dx += dx / d * f; nodes[i].dy += dy / d * f;
      nodes[j].dx -= dx / d * f; nodes[j].dy -= dy / d * f;
    }
    edges.forEach(function (e) {
      var a = nodes[idx[e.source]], b = nodes[idx[e.target]];
      var dx = a.x - b.x, dy = a.y - b.y, d = Math.sqrt(dx * dx + dy * dy) || 0.01, f = d * d / kf;
      a.dx -= dx / d * f; a.dy -= dy / d * f; b.dx += dx / d * f; b.dy += dy / d * f;
    });
    nodes.forEach(function (nd) {
      nd.dx += (w / 2 - nd.x) * 0.02; nd.dy += (h / 2 - nd.y) * 0.02;
      var d = Math.sqrt(nd.dx * nd.dx + nd.dy * nd.dy) || 0.01, m = Math.min(d, t);
      nd.x = Math.min(w - 40, Math.max(40, nd.x + nd.dx / d * m));
      nd.y = Math.min(h - 40, Math.max(40, nd.y + nd.dy / d * m));
    });
    t *= 0.985;
  }
}

function renderGraph(box) {
  if (!box) return;
  fetch(box.getAttribute('data-url'), { credentials: 'same-origin' })
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function (data) {
      if (!data.nodes.length) { box.innerHTML = '<p class="muted" style="padding:1rem">There are no subjects to draw.</p>'; return; }
      var W = 960, H = 620, nodes = data.nodes, edges = data.edges, byId = {};
      layout(nodes, edges, W, H);
      nodes.forEach(function (n) { byId[n.id] = n; });
      var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img' });
      var maxW = Math.max.apply(null, edges.map(function (e) { return e.weight; }).concat([1]));
      edges.forEach(function (e) {
        var a = byId[e.source], b = byId[e.target];
        var line = svgEl('line', { x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: '#7d8ea0',
          'stroke-opacity': 0.25 + 0.5 * e.weight / maxW, 'stroke-width': 0.6 + 2.4 * e.weight / maxW }, svg);
        svgEl('title', {}, line).textContent = a.code + ' - ' + b.code + ': ' + e.weight + ' shared student(s)';
      });
      var used = {};
      nodes.forEach(function (n) {
        var g = svgEl('g', {}, svg);
        var fill = n.color === null ? '#9aa7b4' : SLOT_COLORS[n.color % SLOT_COLORS.length];
        if (n.color !== null) used[n.color] = true;
        svgEl('circle', { cx: n.x, cy: n.y, r: 20, fill: fill, stroke: '#1b2a3a', 'stroke-width': 1.5 }, g);
        var label = svgEl('text', { x: n.x, y: n.y + 4, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 700, fill: '#fff' }, g);
        label.textContent = n.code;
        svgEl('title', {}, g).textContent = n.code + ' ' + n.name + '\nStudents: ' + n.enrollment +
          '\nConflicts with ' + n.degree + ' subject(s)' + (n.color === null ? '' : '\nColor (slot group): ' + n.color);
      });
      box.innerHTML = ''; box.appendChild(svg);
      var legend = document.getElementById('graph-legend');
      if (legend && data.algorithm) {
        legend.innerHTML = '<strong>Colors from ' + data.algorithm + ':</strong>';
        Object.keys(used).sort(function (a, b) { return a - b; }).forEach(function (c) {
          var s = document.createElement('span');
          s.innerHTML = '<span class="dot" style="background:' + SLOT_COLORS[c % SLOT_COLORS.length] + '"></span>Color ' + c;
          legend.appendChild(s);
        });
      }
    })
    .catch(function (err) { box.innerHTML = '<p class="error-text" style="padding:1rem">Could not load the graph: ' + err.message + '</p>'; });
}

function renderCompareCharts() {
  var holder = document.getElementById('compare-data');
  if (!holder) return;
  if (typeof Chart === 'undefined') {
    document.querySelectorAll('canvas').forEach(function (c) {
      c.insertAdjacentHTML('afterend', '<p class="muted">Charts need internet access to load Chart.js. The table above has the same values.</p>');
    });
    return;
  }
  var rows = JSON.parse(holder.textContent);
  var labels = rows.map(function (r) { return r.name.replace(' Coloring', ''); });
  var colors = rows.map(function (r, i) { return SLOT_COLORS[i]; });
  function bar(id, values, title) {
    new Chart(document.getElementById(id), {
      type: 'bar',
      data: { labels: labels, datasets: [{ label: title, data: values, backgroundColor: colors }] },
      options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (c) {
        return rows[c.dataIndex].status === 'timed_out' ? 'Timed out' : c.formattedValue; } } } },
        scales: { y: { beginAtZero: true, title: { display: true, text: title } } } }
    });
  }
  bar('chart-slots', rows.map(function (r) { return r.status === 'timed_out' ? null : r.slots_used; }), 'Slots used');
  bar('chart-runtime', rows.map(function (r) { return r.status === 'timed_out' ? null : +(r.runtime * 1000).toFixed(4); }), 'Milliseconds');
}
