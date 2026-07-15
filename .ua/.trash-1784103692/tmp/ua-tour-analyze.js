#!/usr/bin/env node
// Phase 1: Graph topology analysis for tour design

const fs = require('fs');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node ua-tour-analyze.js <input.json> <output.json>');
  process.exit(1);
}

let input;
try {
  input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
} catch (e) {
  console.error('Failed to read/parse input:', e.message);
  process.exit(1);
}

const { nodes, edges, layers } = input;

// Build node map
const nodeMap = {};
for (const n of nodes) {
  nodeMap[n.id] = n;
}

// A. Fan-In Ranking
const fanIn = {};
const fanOut = {};
for (const n of nodes) {
  fanIn[n.id] = 0;
  fanOut[n.id] = 0;
}
for (const e of edges) {
  if (fanIn[e.target] !== undefined) fanIn[e.target]++;
  if (fanOut[e.source] !== undefined) fanOut[e.source]++;
}

const fanInRanking = Object.entries(fanIn)
  .sort((a, b) => b[1] - a[1])
  .slice(0, 20)
  .map(([id, count]) => ({ id, fanIn: count, name: nodeMap[id]?.name || id }));

const fanOutRanking = Object.entries(fanOut)
  .sort((a, b) => b[1] - a[1])
  .slice(0, 20)
  .map(([id, count]) => ({ id, fanOut: count, name: nodeMap[id]?.name || id }));

// B. Entry Point Candidates
const entryFileNames = new Set([
  'index.ts','index.js','main.ts','main.js','app.ts','app.js',
  'server.ts','server.js','mod.rs','main.go','main.py','main.rs',
  'manage.py','app.py','wsgi.py','asgi.py','run.py','__main__.py',
  'Application.java','Main.java','Program.cs','config.ru','index.php',
  'App.swift','Application.kt','main.cpp','main.c'
]);

const totalNodes = nodes.length;
const fanOutValues = Object.values(fanOut).sort((a, b) => a - b);
const fanInValues = Object.values(fanIn).sort((a, b) => a - b);
const top10PctFanOut = fanOutValues[Math.floor(totalNodes * 0.9)] || 0;
const bottom25PctFanIn = fanInValues[Math.floor(totalNodes * 0.25)] || 0;

const entryScores = [];
for (const n of nodes) {
  let score = 0;
  const fp = n.filePath || '';
  const name = n.name || '';
  const depth = fp.split('/').length - 1;

  if (n.type === 'document' && name === 'README.md' && depth === 0) {
    score += 5;
  } else if (n.type === 'document' && name.endsWith('.md') && depth === 0) {
    score += 2;
  } else if (n.type === 'file') {
    if (entryFileNames.has(name)) score += 3;
    if (depth <= 1) score += 1;
    if (fanOut[n.id] >= top10PctFanOut) score += 1;
    if (fanIn[n.id] <= bottom25PctFanIn) score += 1;
  }

  if (score > 0) {
    entryScores.push({ id: n.id, score, name, summary: n.summary || '' });
  }
}
entryScores.sort((a, b) => b.score - a.score);
const entryPointCandidates = entryScores.slice(0, 5);

// C. BFS Traversal from top code entry point
const codeEntry = entryScores.find(e => nodeMap[e.id]?.type === 'file');
const bfsStart = codeEntry ? codeEntry.id : null;

const bfsResult = { startNode: bfsStart, order: [], depthMap: {}, byDepth: {} };

if (bfsStart) {
  // Build adjacency: forward edges (imports, calls)
  const adj = {};
  for (const n of nodes) adj[n.id] = [];
  for (const e of edges) {
    if ((e.type === 'imports' || e.type === 'calls') && adj[e.source]) {
      adj[e.source].push(e.target);
    }
  }

  const visited = new Set();
  const queue = [{ id: bfsStart, depth: 0 }];
  visited.add(bfsStart);

  while (queue.length > 0) {
    const { id, depth } = queue.shift();
    bfsResult.order.push(id);
    bfsResult.depthMap[id] = depth;
    if (!bfsResult.byDepth[depth]) bfsResult.byDepth[depth] = [];
    bfsResult.byDepth[depth].push(id);

    for (const neighbor of (adj[id] || [])) {
      if (!visited.has(neighbor) && nodeMap[neighbor]) {
        visited.add(neighbor);
        queue.push({ id: neighbor, depth: depth + 1 });
      }
    }
  }
}

// D. Non-code files
const nonCodeFiles = { documentation: [], infrastructure: [], data: [], config: [] };
for (const n of nodes) {
  const entry = { id: n.id, name: n.name, type: n.type, summary: n.summary || '' };
  if (n.type === 'document') nonCodeFiles.documentation.push(entry);
  else if (['service','pipeline','resource'].includes(n.type)) nonCodeFiles.infrastructure.push(entry);
  else if (['table','schema','endpoint'].includes(n.type)) nonCodeFiles.data.push(entry);
  else if (n.type === 'config') nonCodeFiles.config.push(entry);
}

// E. Clusters (tightly coupled nodes)
// Build bidirectional edge map
const edgeSet = new Set();
for (const e of edges) edgeSet.add(`${e.source}|${e.target}`);

const bidirectionalPairs = [];
for (const e of edges) {
  if (edgeSet.has(`${e.target}|${e.source}`) && e.source < e.target) {
    bidirectionalPairs.push([e.source, e.target]);
  }
}

// Build clusters by expanding from pairs
const clusters = [];
for (const [a, b] of bidirectionalPairs) {
  clusters.push({ nodes: [a, b], edgeCount: 2 });
}

// Expand: add nodes with 2+ connections to existing cluster members
for (const cluster of clusters) {
  for (const n of nodes) {
    if (cluster.nodes.includes(n.id)) continue;
    let connections = 0;
    for (const cn of cluster.nodes) {
      if (edgeSet.has(`${n.id}|${cn}`) || edgeSet.has(`${cn}|${n.id}`)) connections++;
    }
    if (connections >= 2) {
      cluster.nodes.push(n.id);
      cluster.edgeCount += connections;
    }
  }
}

// Deduplicate and sort by edgeCount
const seenClusters = new Set();
const uniqueClusters = [];
for (const c of clusters) {
  const key = [...c.nodes].sort().join(',');
  if (!seenClusters.has(key) && c.nodes.length >= 2) {
    seenClusters.add(key);
    uniqueClusters.push(c);
  }
}
uniqueClusters.sort((a, b) => b.edgeCount - a.edgeCount);

// F. Node Summary Index
const nodeSummaryIndex = {};
for (const n of nodes) {
  nodeSummaryIndex[n.id] = { name: n.name, type: n.type, summary: n.summary || '' };
}

// G. Layers
const layersList = {
  count: layers.length,
  list: layers.map(l => ({ id: l.id, name: l.name, description: l.description }))
};

const output = {
  scriptCompleted: true,
  entryPointCandidates,
  fanInRanking,
  fanOutRanking,
  bfsTraversal: bfsResult,
  nonCodeFiles,
  clusters: uniqueClusters.slice(0, 10),
  layers: layersList,
  nodeSummaryIndex,
  totalNodes: nodes.length,
  totalEdges: edges.length
};

try {
  fs.writeFileSync(outputPath, JSON.stringify(output, null, 2));
  console.log('Analysis complete. Output written to', outputPath);
} catch (e) {
  console.error('Failed to write output:', e.message);
  process.exit(1);
}
