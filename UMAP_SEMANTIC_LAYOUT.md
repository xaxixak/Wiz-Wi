# True Vector-Based Semantic Layout (UMAP)

## Current State vs. True Semantic

### What We Have Now (Tag-Based Semantic)
- **Current "Semantic" layout**: Groups nodes by concept/category tags (exact string matching)
- **Concept Clusters**: Visual hulls around nodes sharing the same tag
- **Limitation**: No understanding of semantic similarity—just keyword matching

### What "True Semantic" Would Be (Vector-Based UMAP)
- **Input**: 1536-dimensional embeddings from Oracle ChromaDB (OpenAI text-embedding-3-small)
- **Process**: UMAP dimensionality reduction to 2D coordinates
- **Output**: Nodes positioned by actual semantic meaning—similar documents cluster together even with different tags
- **Example**: `"semantic knowledge graph"`, `"vector architecture"`, `"enriched oracle"` would cluster together even if they don't share exact concept tags

---

## Implementation Plan

### Architecture Decision: **Server-Side UMAP** (Recommended)

**Why server-side:**
1. **Performance**: UMAP computation is CPU-intensive, better on server
2. **Caching**: Can cache UMAP results, instant on re-load
3. **Data transfer**: Sending 2D coordinates (2 floats × N nodes) vs 1536-dimensional vectors (1536 floats × N nodes)
4. **Client simplicity**: Viewer just applies positions, no ML computation

---

## Step-by-Step Implementation

### **Phase 1: Oracle API Endpoint**

**File**: Oracle v2 server (likely `mcp-server-oracle-v2/src/server.py` or similar)

**New Endpoint**: `GET /api/umap`

```python
# Add to Oracle v2 API server
from umap import UMAP
import chromadb
import numpy as np

@app.route('/api/umap', methods=['GET'])
def get_umap_layout():
    """Return UMAP-projected 2D coordinates for all documents."""
    limit = int(request.args.get('limit', 2500))

    # Fetch embeddings from ChromaDB
    collection = chroma_client.get_collection("oracle_documents")
    results = collection.get(limit=limit, include=['embeddings', 'metadatas'])

    doc_ids = results['ids']
    embeddings = np.array(results['embeddings'])  # Shape: (N, 1536)

    # Run UMAP: 1536D → 2D
    reducer = UMAP(
        n_components=2,
        n_neighbors=15,
        min_dist=0.1,
        metric='cosine',
        random_state=42  # reproducible results
    )
    coords_2d = reducer.fit_transform(embeddings)  # Shape: (N, 2)

    # Normalize to [0, 800] x [0, 600] canvas coords
    x_min, x_max = coords_2d[:, 0].min(), coords_2d[:, 0].max()
    y_min, y_max = coords_2d[:, 1].min(), coords_2d[:, 1].max()

    canvas_w, canvas_h = 800, 600
    margin = 50

    layout = {}
    for i, doc_id in enumerate(doc_ids):
        x = margin + ((coords_2d[i, 0] - x_min) / (x_max - x_min)) * (canvas_w - 2*margin)
        y = margin + ((coords_2d[i, 1] - y_min) / (y_max - y_min)) * (canvas_h - 2*margin)
        layout[doc_id] = {"x": float(x), "y": float(y)}

    return jsonify(layout)
```

**Dependencies**: Add to `requirements.txt`
```txt
umap-learn==0.5.5
```

---

### **Phase 2: Viewer Integration**

**File**: `workspace-intelligence/viewer/index.html`

**Add new layout mode**: `"semantic-vector"`

```javascript
// 1. Add to layout dropdown (after "semantic")
<option value="semantic-vector">Semantic (Vector)</option>

// 2. Add handler in setLayoutMode()
else if (mode === 'semantic-vector') {
  applySemanticVectorLayout(nodes);
}

// 3. Implement the layout function
async function applySemanticVectorLayout(nodes) {
  console.log('[Semantic-Vector] Fetching UMAP layout from Oracle...');

  try {
    const resp = await fetch('http://localhost:47778/api/umap?limit=2500');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    const layout = await resp.json();  // {doc_id: {x, y}, ...}

    // Apply positions to nodes
    for (const n of nodes) {
      const pos = layout[n.id];
      if (pos) {
        n.x = pos.x;
        n.y = pos.y;
        n.fx = pos.x;
        n.fy = pos.y;
      } else {
        // Fallback for nodes without embeddings (orphans)
        n.x = Math.random() * 800;
        n.y = Math.random() * 600;
        n.fx = n.x;
        n.fy = n.y;
      }
    }

    console.log('[Semantic-Vector] Applied UMAP positions to', nodes.length, 'nodes');
    simulation.stop();
    ticked();

  } catch (err) {
    console.error('[Semantic-Vector] Failed to fetch UMAP layout:', err);
    alert('Vector-based semantic layout requires Oracle v2 with UMAP endpoint.\n\nFalling back to tag-based semantic layout.');
    applySemanticLayout(nodes);
    simulation.stop();
    ticked();
  }
}

// 4. Add 3D support
function apply3DSemanticVectorLayout(nodes) {
  // Same as apply3DSemanticLayout — project UMAP x,y to flat 3D plane
  for (const d of nodes) {
    const mesh = threeNodeMeshes.get(d.id);
    if (!mesh) continue;
    const x = (d.x || 0) - 400;
    const z = (d.y || 0) - 300;
    mesh.position.set(x, 0, z);
  }
}
```

---

### **Phase 3: Optimization & Caching**

**On Oracle Server**: Cache UMAP results to avoid recomputation

```python
import hashlib
import json
import os

CACHE_DIR = ".umap_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def get_cache_key(doc_ids):
    """Generate cache key from sorted doc IDs."""
    key_str = json.dumps(sorted(doc_ids))
    return hashlib.md5(key_str.encode()).hexdigest()

@app.route('/api/umap', methods=['GET'])
def get_umap_layout():
    limit = int(request.args.get('limit', 2500))

    results = collection.get(limit=limit, include=['embeddings', 'metadatas'])
    doc_ids = results['ids']

    # Check cache
    cache_key = get_cache_key(doc_ids)
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")

    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            return jsonify(json.load(f))

    # Compute UMAP (expensive)
    embeddings = np.array(results['embeddings'])
    reducer = UMAP(n_components=2, n_neighbors=15, min_dist=0.1, metric='cosine', random_state=42)
    coords_2d = reducer.fit_transform(embeddings)

    # ... normalize and build layout ...

    # Save to cache
    with open(cache_file, 'w') as f:
        json.dump(layout, f)

    return jsonify(layout)
```

**Cache invalidation**: Clear `.umap_cache/` when new documents are added to Oracle.

---

## Alternative Approaches

### **Option 2: Precompute UMAP in Oracle DB**

Store UMAP coordinates directly in Oracle's SQLite database:

```sql
ALTER TABLE documents ADD COLUMN umap_x REAL;
ALTER TABLE documents ADD COLUMN umap_y REAL;
```

**When to recompute**:
- After adding new documents (batch job)
- User triggers via UI button: "Recompute semantic layout"

**Pros**: Instant loading, no API call needed
**Cons**: Requires Oracle schema changes, stale if not recomputed

---

### **Option 3: Client-Side Dimensionality Reduction (Not Recommended)**

Use **PCA** instead of UMAP (simpler, runs in browser):

```javascript
// Fetch raw embeddings
const resp = await fetch('http://localhost:47778/api/embeddings');
const embeddings = await resp.json();  // {doc_id: [1536 floats], ...}

// Run PCA in browser (need a JS library like ml-pca)
import PCA from 'ml-pca';
const pca = new PCA(embeddings_matrix);
const coords = pca.predict(embeddings_matrix, {nComponents: 2});
```

**Why not recommended**:
- Large data transfer (1536 floats × N nodes)
- PCA is linear, less effective than UMAP for high-dimensional data
- Browser computation lag for 1000+ nodes

---

## Testing the Implementation

1. **Start Oracle v2 with UMAP endpoint**:
   ```bash
   cd ~/ghq/.../mcp-server-oracle-v2
   pip install umap-learn
   python src/server.py
   ```

2. **Test endpoint directly**:
   ```bash
   curl http://localhost:47778/api/umap?limit=100 | jq
   ```
   Expected output:
   ```json
   {
     "learning_2026-01-15_hooks": {"x": 234.5, "y": 456.7},
     "principle_3_nothing-deleted": {"x": 123.4, "y": 789.0},
     ...
   }
   ```

3. **In viewer**: Select "Semantic (Vector)" from layout dropdown

4. **Verify clustering**: Semantically similar documents should be visually close even if they don't share tags

---

## Expected Results

### Before (Tag-Based Semantic)
- Nodes with same tag `"coda"` cluster together
- Nodes with tags `"coda-plugin"` and `"mcp-design"` are far apart (different strings)

### After (Vector-Based UMAP Semantic)
- Nodes about similar topics cluster together even with different tags
- `"coda-plugin"`, `"coda-mcp-integration"`, `"oracle-mcp"` all cluster together (related concepts)
- Visual "semantic neighborhoods" emerge: MCP cluster, Oracle cluster, CODA cluster, etc.

---

## Next Steps

1. **Locate Oracle v2 server code** (check `~/ghq/`, temp folders, or installed MCP servers)
2. **Add UMAP endpoint** (10-20 lines of Python)
3. **Install umap-learn** (`pip install umap-learn`)
4. **Wire viewer** (already prepared in this guide)
5. **Test with small dataset** (50-100 docs) before full graph

---

## Questions?

- **"How long does UMAP take?"** ~2-5 seconds for 500 docs, ~10-20 seconds for 2000 docs (first time; cached after that)
- **"Can we use t-SNE instead?"** Yes, but UMAP is faster and better at preserving global structure
- **"What if Oracle doesn't have embeddings?"** Tag-based semantic layout (current) is the fallback
- **"Can we update positions live?"** Not recommended—UMAP is expensive; run once, cache, reuse
