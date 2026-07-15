#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const inputPath = process.argv[2];
const outputPath = process.argv[3];

if (!inputPath || !outputPath) {
  console.error('Usage: node ua-arch-analyze.js <input.json> <output.json>');
  process.exit(1);
}

let data;
try {
  data = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
} catch (e) {
  console.error('Failed to read/parse input:', e.message);
  process.exit(1);
}

const { fileNodes, importEdges, allEdges } = data;

// ─── A. Directory Grouping ────────────────────────────────────────────────────
function getFilePath(node) {
  return node.filePath || '';
}

// Compute common prefix
const allPaths = fileNodes.map(getFilePath).filter(Boolean);

function commonPrefix(paths) {
  if (!paths.length) return '';
  const parts = paths[0].split('/');
  let prefix = [];
  for (let i = 0; i < parts.length - 1; i++) {
    const seg = parts[i];
    if (paths.every(p => p.split('/')[i] === seg)) {
      prefix.push(seg);
    } else {
      break;
    }
  }
  return prefix.length ? prefix.join('/') + '/' : '';
}

const commonPfx = commonPrefix(allPaths);

function getGroupKey(filePath) {
  let stripped = filePath;
  if (commonPfx && filePath.startsWith(commonPfx)) {
    stripped = filePath.slice(commonPfx.length);
  }
  const segments = stripped.split('/');
  if (segments.length === 1) return 'root';
  // Use first segment after prefix
  return segments[0];
}

const directoryGroups = {};
for (const node of fileNodes) {
  const group = getGroupKey(node.filePath);
  if (!directoryGroups[group]) directoryGroups[group] = [];
  directoryGroups[group].push(node.id);
}

// ─── B. Node Type Grouping ────────────────────────────────────────────────────
const nodeTypeGroups = {};
for (const node of fileNodes) {
  const t = node.type || 'file';
  if (!nodeTypeGroups[t]) nodeTypeGroups[t] = [];
  nodeTypeGroups[t].push(node.id);
}

// ─── C. Import Adjacency / Fan-in / Fan-out ───────────────────────────────────
const fanIn = {};
const fanOut = {};
const nodeById = {};
for (const n of fileNodes) {
  nodeById[n.id] = n;
  fanIn[n.id] = 0;
  fanOut[n.id] = 0;
}

for (const e of importEdges) {
  if (fanOut[e.source] !== undefined) fanOut[e.source]++;
  if (fanIn[e.target] !== undefined) fanIn[e.target]++;
}

// ─── D. Cross-Category Dependency Analysis ────────────────────────────────────
const crossCategoryMap = {};
for (const e of allEdges) {
  const srcNode = nodeById[e.source];
  const tgtNode = nodeById[e.target];
  if (!srcNode || !tgtNode) continue;
  const key = `${srcNode.type}|${tgtNode.type}|${e.type}`;
  crossCategoryMap[key] = (crossCategoryMap[key] || 0) + 1;
}
const crossCategoryEdges = Object.entries(crossCategoryMap).map(([k, count]) => {
  const [fromType, toType, edgeType] = k.split('|');
  return { fromType, toType, edgeType, count };
});

// ─── E. Inter-Group Import Frequency ─────────────────────────────────────────
function nodeGroup(nodeId) {
  const node = nodeById[nodeId];
  if (!node) return 'unknown';
  return getGroupKey(node.filePath);
}

const interGroupMap = {};
for (const e of importEdges) {
  const fromG = nodeGroup(e.source);
  const toG = nodeGroup(e.target);
  if (fromG === toG) continue;
  const key = `${fromG}|${toG}`;
  interGroupMap[key] = (interGroupMap[key] || 0) + 1;
}
const interGroupImports = Object.entries(interGroupMap).map(([k, count]) => {
  const [from, to] = k.split('|');
  return { from, to, count };
}).sort((a, b) => b.count - a.count);

// ─── F. Intra-Group Import Density ───────────────────────────────────────────
const groupTotalEdges = {};
const groupInternalEdges = {};
for (const g of Object.keys(directoryGroups)) {
  groupTotalEdges[g] = 0;
  groupInternalEdges[g] = 0;
}

for (const e of importEdges) {
  const fromG = nodeGroup(e.source);
  const toG = nodeGroup(e.target);
  if (groupTotalEdges[fromG] !== undefined) groupTotalEdges[fromG]++;
  if (groupTotalEdges[toG] !== undefined) groupTotalEdges[toG]++;
  if (fromG === toG && groupInternalEdges[fromG] !== undefined) groupInternalEdges[fromG]++;
}

const intraGroupDensity = {};
for (const g of Object.keys(directoryGroups)) {
  const total = groupTotalEdges[g] || 0;
  const internal = groupInternalEdges[g] || 0;
  intraGroupDensity[g] = {
    internalEdges: internal,
    totalEdges: total,
    density: total > 0 ? parseFloat((internal / total).toFixed(3)) : 0
  };
}

// ─── G. Directory Pattern Matching ───────────────────────────────────────────
const DIR_PATTERNS = {
  routes: 'api', api: 'api', controllers: 'api', endpoints: 'api', handlers: 'api',
  serializers: 'api', blueprints: 'api', routers: 'api', controller: 'api',
  services: 'service', core: 'service', lib: 'service', domain: 'service',
  logic: 'service', composables: 'service', signals: 'service',
  mailers: 'service', jobs: 'service', channels: 'service',
  internal: 'service',
  models: 'data', db: 'data', data: 'data', persistence: 'data',
  repository: 'data', entities: 'data', migrations: 'data',
  entity: 'data', sql: 'data', database: 'data', schema: 'data',
  components: 'ui', views: 'ui', pages: 'ui', ui: 'ui',
  layouts: 'ui', screens: 'ui',
  middleware: 'middleware', plugins: 'middleware', interceptors: 'middleware', guards: 'middleware',
  utils: 'utility', helpers: 'utility', common: 'utility', shared: 'utility',
  tools: 'utility', pkg: 'utility', templatetags: 'utility',
  config: 'config', constants: 'config', env: 'config', settings: 'config',
  management: 'config', commands: 'config',
  '__tests__': 'test', test: 'test', tests: 'test', spec: 'test', specs: 'test',
  types: 'types', interfaces: 'types', schemas: 'types', contracts: 'types',
  dtos: 'types', dto: 'types', request: 'types', response: 'types',
  hooks: 'hooks',
  store: 'state', state: 'state', reducers: 'state', actions: 'state', slices: 'state',
  assets: 'assets', static: 'assets', 'public': 'assets',
  cmd: 'entry', bin: 'entry',
  docs: 'documentation', documentation: 'documentation', wiki: 'documentation',
  deploy: 'infrastructure', deployment: 'infrastructure', infra: 'infrastructure',
  infrastructure: 'infrastructure', k8s: 'infrastructure', kubernetes: 'infrastructure',
  helm: 'infrastructure', charts: 'infrastructure', terraform: 'infrastructure',
  tf: 'infrastructure', docker: 'infrastructure',
  '.github': 'ci-cd', '.gitlab': 'ci-cd', '.circleci': 'ci-cd',
  engines: 'engines',
  examples: 'examples',
  scripts: 'scripts',
  demo_configs: 'config',
  configs: 'config',
  benchmark_scripts: 'test'
};

const patternMatches = {};
for (const g of Object.keys(directoryGroups)) {
  patternMatches[g] = DIR_PATTERNS[g] || 'unknown';
}

// ─── H. Deployment Topology Detection ────────────────────────────────────────
const infraPatterns = [
  /^Dockerfile/, /^docker-compose/, /\.tf$/, /\.tfvars$/,
  /^\.github\//, /^\.gitlab-ci/, /^Jenkinsfile/, /^Makefile$/,
  /kubernetes/, /k8s/, /helm/
];
const infraFiles = fileNodes
  .filter(n => infraPatterns.some(p => p.test(n.filePath)))
  .map(n => n.filePath);

const deploymentTopology = {
  hasDockerfile: fileNodes.some(n => /Dockerfile/.test(n.filePath)),
  hasCompose: fileNodes.some(n => /docker-compose/.test(n.filePath)),
  hasK8s: fileNodes.some(n => /k8s|kubernetes|helm/.test(n.filePath)),
  hasTerraform: fileNodes.some(n => /\.tf$|\.tfvars$/.test(n.filePath)),
  hasCI: fileNodes.some(n => /\.github\/|\.gitlab-ci|Jenkinsfile/.test(n.filePath)),
  infraFiles
};

// ─── I. Data Pipeline Detection ───────────────────────────────────────────────
const schemaFiles = fileNodes.filter(n => /\.(graphql|gql|proto|sql|prisma)$/.test(n.filePath)).map(n => n.filePath);
const migrationFiles = fileNodes.filter(n => /migration/.test(n.filePath) && /\.sql$/.test(n.filePath)).map(n => n.filePath);
const dataModelFiles = fileNodes.filter(n => n.tags && n.tags.includes('data-model')).map(n => n.filePath);
const apiHandlerFiles = fileNodes.filter(n => n.tags && n.tags.includes('api-handler')).map(n => n.filePath);

const dataPipeline = { schemaFiles, migrationFiles, dataModelFiles, apiHandlerFiles };

// ─── J. Documentation Coverage ───────────────────────────────────────────────
const groups = Object.keys(directoryGroups);
const groupsWithDocs = groups.filter(g =>
  directoryGroups[g].some(id => {
    const n = nodeById[id];
    return n && (n.type === 'document' || (n.tags && n.tags.includes('docs')));
  })
);
const undocumentedGroups = groups.filter(g => !groupsWithDocs.includes(g));
const docCoverage = {
  groupsWithDocs: groupsWithDocs.length,
  totalGroups: groups.length,
  coverageRatio: parseFloat((groupsWithDocs.length / groups.length).toFixed(2)),
  undocumentedGroups
};

// ─── K. Dependency Direction ──────────────────────────────────────────────────
const pairMap = {};
for (const e of importEdges) {
  const fromG = nodeGroup(e.source);
  const toG = nodeGroup(e.target);
  if (fromG === toG) continue;
  const key = [fromG, toG].sort().join('|');
  if (!pairMap[key]) pairMap[key] = { a: fromG, b: toG, aToB: 0, bToA: 0 };
  const entry = pairMap[key];
  if (fromG === entry.a) entry.aToB++;
  else entry.bToA++;
}

const dependencyDirection = Object.values(pairMap).map(({ a, b, aToB, bToA }) => {
  if (aToB >= bToA) return { dependent: a, dependsOn: b };
  return { dependent: b, dependsOn: a };
});

// ─── File Stats ───────────────────────────────────────────────────────────────
const filesPerGroup = {};
for (const [g, ids] of Object.entries(directoryGroups)) filesPerGroup[g] = ids.length;

const nodeTypeCounts = {};
for (const [t, ids] of Object.entries(nodeTypeGroups)) nodeTypeCounts[t] = ids.length;

const fileStats = {
  totalFileNodes: fileNodes.length,
  filesPerGroup,
  nodeTypeCounts
};

// Top fan-in / fan-out (top 10)
const fileFanIn = Object.fromEntries(
  Object.entries(fanIn).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 20)
);
const fileFanOut = Object.fromEntries(
  Object.entries(fanOut).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 20)
);

// ─── Output ───────────────────────────────────────────────────────────────────
const result = {
  scriptCompleted: true,
  directoryGroups,
  nodeTypeGroups,
  crossCategoryEdges,
  interGroupImports,
  intraGroupDensity,
  patternMatches,
  deploymentTopology,
  dataPipeline,
  docCoverage,
  dependencyDirection,
  fileStats,
  fileFanIn,
  fileFanOut
};

try {
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2));
  console.log('Analysis complete. Output written to', outputPath);
} catch (e) {
  console.error('Failed to write output:', e.message);
  process.exit(1);
}
