"""
Test script for Pass 2: Pattern Matching

Demonstrates the pattern matching capabilities of the PatternPass.
"""

from pathlib import Path
from graph_store import GraphStore
from pipeline.pass2_patterns import PatternPass
from ontology import NodeType, EdgeType


def test_pattern_pass():
    """Test the pattern matching pass."""
    # Create a temporary test file
    test_file = Path("test_sample.js")
    test_content = """
// Express routes
app.get('/api/users', async (req, res) => {
    // Get all users
});

router.post('/api/orders', createOrder);

// Mongoose model
const userSchema = new mongoose.Schema({
    name: String,
    email: String,
});

const User = mongoose.model('User', userSchema);

// Event emitter
eventEmitter.emit('user.created', userData);
socket.on('message', handleMessage);

// Queue operations
const emailQueue = new Queue('email-queue');

@Process('email-queue')
async processEmail(job) {
    // Process email
}

// Environment variables
const apiKey = process.env.API_KEY;
const dbUrl = process.env.DATABASE_URL;

// Cache operations
await redis.get('user:123:profile');
await cache.set('session:abc', sessionData);

// Middleware
app.use(authMiddleware);
router.use(loggingMiddleware);
"""

    try:
        test_file.write_text(test_content)

        # Initialize store and pattern pass
        store = GraphStore()
        pattern_pass = PatternPass(store)

        # Create a File node first (simulating Pass 1)
        from ontology import GraphNode, SourceLocation, Provenance

        file_node = GraphNode(
            id="file:test-project:test_sample.js",
            type=NodeType.FILE,
            name="test_sample.js",
            location=SourceLocation(
                file_path="test_sample.js",
                start_line=1,
                end_line=1,
            ),
            provenance=Provenance.SCANNER,
            language="javascript",
        )
        store.add_node(file_node)

        # Process the file
        print("Processing test file...")
        nodes = pattern_pass.process_file(
            file_path=test_file,
            project_id="test-project",
            language="javascript",
        )

        print(f"\n[OK] Created {len(nodes)} nodes from pattern matching\n")

        # Display discovered nodes by type
        node_types = {}
        for node in nodes:
            node_type = node.type.value
            if node_type not in node_types:
                node_types[node_type] = []
            node_types[node_type].append(node)

        for node_type, type_nodes in sorted(node_types.items()):
            print(f"\n{node_type.upper()} ({len(type_nodes)}):")
            print("-" * 50)
            for node in type_nodes:
                print(f"  • {node.name}")
                if node.metadata:
                    for key, value in node.metadata.items():
                        if value:
                            print(f"    - {key}: {value}")

        # Display edges
        print(f"\n\nEDGES:")
        print("-" * 50)
        defines_edges = store.get_edges_by_type(EdgeType.DEFINES)
        print(f"Created {len(defines_edges)} DEFINES edges from File to discovered nodes")

        # Display stats
        print(f"\n\nGRAPH STATISTICS:")
        print("-" * 50)
        stats = store.stats()
        print(f"Total Nodes: {stats['total_nodes']}")
        print(f"Total Edges: {stats['total_edges']}")
        print("\nNodes by Type:")
        for node_type, count in stats['nodes_by_type'].items():
            if count > 0:
                print(f"  {node_type}: {count}")

    finally:
        # Clean up test file
        if test_file.exists():
            test_file.unlink()


if __name__ == "__main__":
    test_pattern_pass()
