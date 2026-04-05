"""
Pass 3 Enrichment — Claude acting as the LLM.

This script applies semantic analysis (file classification, descriptions,
tags, and missing edges) that Pass 3 would normally do via the Anthropic API.
Instead, the analysis was done by Claude in-session and hardcoded here.

Usage:
  python scripts/claude_pass3_enrich.py graphs/test-shop_graph.json
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graph_store import GraphStore
from ontology import GraphNode, GraphEdge, NodeType, EdgeType, Provenance, SourceLocation


def enrich_graph(store: GraphStore) -> dict:
    """Apply Claude's semantic analysis to the graph."""
    stats = {"descriptions_added": 0, "tags_added": 0, "classifications": 0, "edges_added": 0, "nodes_added": 0}

    # =========================================================================
    # 1. FILE CLASSIFICATIONS — role, description, tags, frameworks
    # =========================================================================
    file_classifications = {
        "app.js": {
            "description": "Application entry point — bootstraps Express, connects DB/Redis, mounts route handlers and middleware",
            "tags": ["entry-point", "bootstrap", "express"],
            "role": "orchestrator",
        },
        "eventBus.js": {
            "description": "Domain event system — pub/sub bus for decoupled communication between modules (orders, payments, users, shipping)",
            "tags": ["event-driven", "pub-sub", "domain-events"],
            "role": "infrastructure",
        },
        "auth.js": {
            "description": "JWT authentication middleware — verifies Bearer tokens from Authorization header, adds userId/role to request",
            "tags": ["security", "jwt", "middleware", "authentication"],
            "role": "middleware",
        },
        "rateLimiter.js": {
            "description": "Rate limiting middleware — limits requests per IP using Redis counter with sliding window",
            "tags": ["security", "rate-limit", "middleware", "redis"],
            "role": "middleware",
        },
        "Order.js": {
            "description": "Order data model — Mongoose schema with items, total, status lifecycle (pending→paid→shipped→delivered→cancelled), payment and shipping tracking",
            "tags": ["model", "mongoose", "orders", "schema"],
            "role": "data-model",
        },
        "Product.js": {
            "description": "Product catalog data model — Mongoose schema with pricing, inventory, categories, images, indexed for search",
            "tags": ["model", "mongoose", "products", "schema", "indexed"],
            "role": "data-model",
        },
        "User.js": {
            "description": "User account data model — Mongoose schema with email/password auth, roles (customer/admin), Stripe customer link, preferences",
            "tags": ["model", "mongoose", "users", "schema", "auth"],
            "role": "data-model",
        },
        "orders.js": {
            "description": "Order management routes — create order (with inventory check, Stripe payment, event emit, queue jobs), list/get/cancel orders with caching",
            "tags": ["routes", "crud", "orders", "business-logic", "critical-path"],
            "role": "route-handler",
        },
        "products.js": {
            "description": "Product catalog routes — list (paginated, cached), get by slug (cached), create/update/delete (admin only, cache invalidation)",
            "tags": ["routes", "crud", "products", "caching", "admin"],
            "role": "route-handler",
        },
        "users.js": {
            "description": "User account routes — register, login (JWT token generation), get/update profile (cached)",
            "tags": ["routes", "auth", "users", "jwt"],
            "role": "route-handler",
        },
        "webhooks.js": {
            "description": "External webhook handlers — Stripe payment events (succeeded/failed), shipping carrier status updates",
            "tags": ["routes", "webhooks", "stripe", "shipping", "external-integration"],
            "role": "integration",
        },
        "cache.js": {
            "description": "Redis cache service — connection management, get/set/delete with TTL, graceful failure handling",
            "tags": ["service", "redis", "caching", "infrastructure"],
            "role": "infrastructure",
        },
        "database.js": {
            "description": "MongoDB connection service — Mongoose connect/disconnect, auto-exit on connection failure",
            "tags": ["service", "mongodb", "mongoose", "infrastructure"],
            "role": "infrastructure",
        },
        "queue.js": {
            "description": "BullMQ job queue service — 3 queues (orders, notifications, analytics), job routing by name, workers with retry/backoff",
            "tags": ["service", "bullmq", "queue", "workers", "background-jobs"],
            "role": "infrastructure",
        },
        "stripe.js": {
            "description": "Stripe payment service — create PaymentIntent, process refunds, retrieve customers",
            "tags": ["service", "stripe", "payments", "external-api"],
            "role": "integration",
        },
    }

    # Apply file classifications
    for node_id, data in list(store.graph.nodes(data=True)):
        if data.get("type") == "File":
            name = data.get("name", "")
            base_name = name.split("/")[-1] if "/" in name else name
            if base_name in file_classifications:
                cls = file_classifications[base_name]
                store.graph.nodes[node_id]["description"] = cls["description"]
                store.graph.nodes[node_id]["tags"] = cls.get("tags", [])
                store.graph.nodes[node_id]["role"] = cls.get("role", "")
                stats["classifications"] += 1
                stats["descriptions_added"] += 1
                stats["tags_added"] += 1

    # =========================================================================
    # 2. FUNCTION DESCRIPTIONS — what each function does semantically
    # =========================================================================
    function_descriptions = {
        "start": "Bootstraps the application: connects MongoDB, initializes Redis cache, sets up event bus, starts Express HTTP server",
        "emitEvent": "Publishes a domain event to the in-memory event bus for decoupled handler notification",
        "onEvent": "Subscribes a handler function to a specific domain event name",
        "setupEventBus": "Registers all domain event listeners: order lifecycle, payment status, user activity, product changes, shipping updates",
        "authMiddleware": "Express middleware that validates JWT Bearer token, extracts userId and role, blocks unauthenticated requests with 401",
        "rateLimiter": "Express middleware that rate-limits by IP address using Redis counter, returns 429 when limit exceeded, degrades gracefully if Redis down",
        "createPaymentIntent": "Creates a Stripe PaymentIntent for the given amount/currency with order metadata, enables automatic payment methods",
        "refundPayment": "Issues a full or partial refund via Stripe Refunds API using the original PaymentIntent ID",
        "getStripeCustomer": "Retrieves a customer record from Stripe by their customer ID",
        "connectDB": "Establishes MongoDB connection via Mongoose, exits process on failure",
        "disconnectDB": "Gracefully disconnects from MongoDB",
        "initRedis": "Creates and connects a Redis client, sets up error logging",
        "getCache": "Reads a value from Redis by key, returns null on miss or error",
        "setCache": "Writes a key-value pair to Redis with TTL (default 5 minutes), silently ignores errors",
        "deleteCache": "Removes a key from Redis cache, used for cache invalidation on writes",
        "enqueueJob": "Routes a named background job to the appropriate BullMQ queue (orders/notifications/analytics) with retry policy",
    }

    for node_id, data in list(store.graph.nodes(data=True)):
        if data.get("type") == "Function":
            name = data.get("name", "")
            if name in function_descriptions:
                store.graph.nodes[node_id]["description"] = function_descriptions[name]
                stats["descriptions_added"] += 1

    # =========================================================================
    # 3. EVENT DESCRIPTIONS
    # =========================================================================
    event_descriptions = {
        "ORDER_CREATED": "Emitted when a new order is placed — carries orderId, userId, total, itemCount",
        "ORDER_CANCELLED": "Emitted when an order is cancelled — triggers refund processing",
        "PAYMENT_RECEIVED": "Emitted on Stripe payment_intent.succeeded webhook — order transitions to 'paid'",
        "PAYMENT_FAILED": "Emitted on Stripe payment_intent.payment_failed — order auto-cancelled",
        "USER_CREATED": "Emitted on new user registration — could trigger welcome email",
        "USER_LOGGED_IN": "Emitted on successful login — tracks user activity",
        "USER_UPDATED": "Emitted when user profile is updated",
        "PRODUCT_CREATED": "Emitted when admin creates a new product",
        "SHIPPING_UPDATE": "Emitted by shipping webhook — tracks delivery status changes",
    }

    for node_id, data in list(store.graph.nodes(data=True)):
        if data.get("type") == "Event":
            name = data.get("name", "")
            if name in event_descriptions:
                store.graph.nodes[node_id]["description"] = event_descriptions[name]
                stats["descriptions_added"] += 1

    # =========================================================================
    # 4. MISSING EVENTS (not detected by regex) — PRODUCT_UPDATED, PRODUCT_DELETED, SHIPPING_UPDATE, PRODUCT_CREATED
    # =========================================================================
    missing_events = [
        ("PRODUCT_UPDATED", "Emitted when admin updates product details — triggers cache invalidation"),
        ("PRODUCT_DELETED", "Emitted when admin soft-deletes a product"),
        ("SHIPPING_UPDATE", "Emitted by shipping webhook — status changes (shipped/delivered)"),
        ("USER_UPDATED", "Emitted when user updates their profile"),
    ]

    workspace_name = "test-shop"
    project_id = None
    # Find the project node ID
    for nid in store.graph.nodes():
        if "project:" in nid and "test-shop" in nid:
            project_id = nid
            break

    for event_name, desc in missing_events:
        event_id = f"event:{project_id}:{event_name}" if project_id else f"event:test-shop:{event_name}"
        if not store.graph.has_node(event_id):
            store.graph.add_node(event_id, **{
                "id": event_id,
                "type": "Event",
                "name": event_name,
                "description": desc,
                "tier": "micro",
                "confidence": 0.85,
                "provenance": "llm",
                "tags": ["domain-event"],
            })
            stats["nodes_added"] += 1

    # =========================================================================
    # 5. MISSING EDGES — semantic connections regex missed
    # =========================================================================
    # These are connections that require understanding code semantics:

    missing_edges = []

    # Find node IDs dynamically
    def find_node(name_contains, type_filter=None):
        """Find a node ID containing the given string."""
        for nid, data in store.graph.nodes(data=True):
            if name_contains.lower() in nid.lower():
                if type_filter is None or data.get("type") == type_filter:
                    return nid
        return None

    # webhooks.js emits SHIPPING_UPDATE (regex might miss the variable event name)
    webhooks_file = find_node("webhooks.js", "File")
    shipping_event = find_node("SHIPPING_UPDATE", "Event")
    if webhooks_file and shipping_event:
        edge_key = (webhooks_file, shipping_event, "EMITS_EVENT")
        if not store.graph.has_edge(webhooks_file, shipping_event):
            store.graph.add_edge(webhooks_file, shipping_event, **{
                "type": "EMITS_EVENT",
                "source": webhooks_file,
                "target": shipping_event,
                "confidence": 0.85,
                "provenance": "llm",
            })
            stats["edges_added"] += 1

    # products.js emits PRODUCT_UPDATED and PRODUCT_DELETED
    products_file = find_node("products.js", "File")
    for evt_name in ["PRODUCT_UPDATED", "PRODUCT_DELETED"]:
        evt_node = find_node(evt_name, "Event")
        if products_file and evt_node:
            if not store.graph.has_edge(products_file, evt_node):
                store.graph.add_edge(products_file, evt_node, **{
                    "type": "EMITS_EVENT",
                    "source": products_file,
                    "target": evt_node,
                    "confidence": 0.85,
                    "provenance": "llm",
                })
                stats["edges_added"] += 1

    # users.js emits USER_UPDATED
    users_file = find_node("users.js", "File")
    user_updated = find_node("USER_UPDATED", "Event")
    if users_file and user_updated:
        if not store.graph.has_edge(users_file, user_updated):
            store.graph.add_edge(users_file, user_updated, **{
                "type": "EMITS_EVENT",
                "source": users_file,
                "target": user_updated,
                "confidence": 0.85,
                "provenance": "llm",
            })
            stats["edges_added"] += 1

    # webhooks.js enqueues send-payment-receipt and send-shipping-notification
    for queue_name in ["send-payment-receipt", "send-shipping-notification"]:
        queue_node = find_node(queue_name, "Queue")
        if not queue_node:
            # Create the queue node
            qid = f"queue:{project_id}:{queue_name}" if project_id else f"queue:test-shop:{queue_name}"
            store.graph.add_node(qid, **{
                "id": qid,
                "type": "Queue",
                "name": queue_name,
                "description": f"Background job queue for {queue_name.replace('-', ' ')}",
                "tier": "micro",
                "confidence": 0.85,
                "provenance": "llm",
                "tags": ["queue", "bullmq"],
            })
            queue_node = qid
            stats["nodes_added"] += 1

        if webhooks_file and queue_node:
            if not store.graph.has_edge(webhooks_file, queue_node):
                store.graph.add_edge(webhooks_file, queue_node, **{
                    "type": "ENQUEUES",
                    "source": webhooks_file,
                    "target": queue_node,
                    "confidence": 0.85,
                    "provenance": "llm",
                })
                stats["edges_added"] += 1

    # =========================================================================
    # 6. ARCHITECTURAL PATTERNS (tags on the store level)
    # =========================================================================
    # Add a description to the Stripe external API node
    stripe_node = find_node("stripe", "ExternalAPI")
    if stripe_node:
        store.graph.nodes[stripe_node]["description"] = "Stripe payment gateway — handles PaymentIntents, Refunds, Customers, Webhooks"
        store.graph.nodes[stripe_node]["tags"] = ["payment-gateway", "external-api", "critical-path"]
        stats["descriptions_added"] += 1

    # Add tags to queue nodes
    for nid, data in list(store.graph.nodes(data=True)):
        if data.get("type") == "Queue":
            name = data.get("name", "")
            if "notification" in name or "confirmation" in name or "receipt" in name:
                store.graph.nodes[nid]["tags"] = ["notification", "email", "async"]
            elif "refund" in name:
                store.graph.nodes[nid]["tags"] = ["payment", "refund", "critical-path"]
            elif "analytics" in name:
                store.graph.nodes[nid]["tags"] = ["analytics", "tracking", "async"]
            stats["tags_added"] += 1

    # Add tags to cache key node
    for nid, data in list(store.graph.nodes(data=True)):
        if data.get("type") == "CacheKey":
            store.graph.nodes[nid]["description"] = "Dynamic Redis cache keys — used for rate limiting, order caching, product caching, user profiles"
            store.graph.nodes[nid]["tags"] = ["redis", "caching", "performance"]
            stats["descriptions_added"] += 1

    return stats


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/claude_pass3_enrich.py <graph.json>")
        sys.exit(1)

    graph_path = sys.argv[1]
    store = GraphStore()
    store.load(graph_path)

    before = store.stats()
    print(f"Before enrichment: {before['total_nodes']} nodes, {before['total_edges']} edges")

    stats = enrich_graph(store)

    after = store.stats()
    print(f"After enrichment:  {after['total_nodes']} nodes, {after['total_edges']} edges")
    print(f"\nEnrichment summary:")
    print(f"  Descriptions added: {stats['descriptions_added']}")
    print(f"  Tags added:         {stats['tags_added']}")
    print(f"  Classifications:    {stats['classifications']}")
    print(f"  New nodes:          {stats['nodes_added']}")
    print(f"  New edges:          {stats['edges_added']}")

    store.save(graph_path)
    print(f"\nSaved to {graph_path}")


if __name__ == "__main__":
    main()
