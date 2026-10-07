"""Пользовательские команды работы с фреймами и семантической сетью."""

import json
from pathlib import Path

from .frames import FrameStore
from .knowledge import ROOT, load_knowledge
from .network import SemanticNetwork


def add_commands(sub):
    frame = sub.add_parser("frame", help="Поиск, создание и изменение фреймов")
    frame.add_argument("--state", type=Path, default=ROOT / ".state/frames.json")
    actions = frame.add_subparsers(dest="action", required=True)
    show = actions.add_parser("show")
    show.add_argument("name")
    find = actions.add_parser("find")
    find.add_argument("slot")
    find.add_argument("value", help="Значение в формате JSON")
    create = actions.add_parser("create")
    create.add_argument("name")
    create.add_argument("parent")
    change = actions.add_parser("set")
    change.add_argument("name")
    change.add_argument("slot")
    change.add_argument("value", help="Значение в формате JSON")
    network = sub.add_parser("network", help="Узлы, связи, поиск связанных объектов и путей")
    network.add_argument("--state", type=Path, default=ROOT / ".state/network.json")
    actions = network.add_subparsers(dest="action", required=True)
    actions.add_parser("nodes")
    node = actions.add_parser("add-node")
    node.add_argument("name")
    node.add_argument("label")
    edge = actions.add_parser("add-edge")
    edge.add_argument("source")
    edge.add_argument("relation")
    edge.add_argument("target")
    for name in ("related", "path"):
        search = actions.add_parser(name)
        search.add_argument("source")
        if name == "related":
            search.add_argument("--depth", type=int, default=2)
        else:
            search.add_argument("target")
        search.add_argument("--relation")
        search.add_argument("--undirected", action="store_true")


def run(args):
    knowledge = load_knowledge()
    if args.command == "frame":
        store = FrameStore.load(args.state, knowledge["frames"])
        if args.action == "show":
            result = store.describe(args.name)
        elif args.action == "find":
            result = store.find(args.slot, json.loads(args.value))
        elif args.action == "create":
            result = store.create(args.name, args.parent)
            store.save(args.state)
        else:
            result = store.set(args.name, args.slot, json.loads(args.value))
            store.save(args.state)
    else:
        store = SemanticNetwork.load(args.state, knowledge["network"])
        if args.action == "nodes":
            result = store.nodes
        elif args.action == "add-node":
            store.add_node(args.name, args.label)
            store.save(args.state)
            result = {args.name: store.nodes[args.name]}
        elif args.action == "add-edge":
            store.add_edge(args.source, args.relation, args.target)
            store.save(args.state)
            result = [args.source, args.relation, args.target]
        elif args.action == "path":
            result = store.path(args.source, args.target, args.relation, args.undirected)
        else:
            result = store.related(args.source, args.depth, args.relation, args.undirected)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
