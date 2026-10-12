from .schema import CADPlan


def example_plan(template: str) -> CADPlan:
    if template == "bracket":
        operations = [
            {"id": "base", "op": "box", "size": [60, 40, 5]},
            {"id": "wall", "op": "box", "size": [60, 5, 45]},
            {
                "id": "wall_pos",
                "op": "translate",
                "inputs": ["wall"],
                "vector": [0, 17.5, 0],
            },
            {"id": "body", "op": "union", "inputs": ["base", "wall_pos"]},
            {"id": "hole", "op": "cylinder", "radius": 3, "height": 8},
            {
                "id": "hole_pos",
                "op": "translate",
                "inputs": ["hole"],
                "vector": [-18, -5, -1],
            },
            {"id": "first", "op": "cut", "inputs": ["body", "hole_pos"]},
            {
                "id": "hole_two",
                "op": "translate",
                "inputs": ["hole"],
                "vector": [18, -5, -1],
            },
            {"id": "result", "op": "cut", "inputs": ["first", "hole_two"]},
        ]
        name = "Mounting bracket"
    elif template == "planter":
        operations = [
            {"id": "outer", "op": "cylinder", "radius": 36, "height": 65},
            {"id": "inner", "op": "cylinder", "radius": 33, "height": 65},
            {
                "id": "inner_pos",
                "op": "translate",
                "inputs": ["inner"],
                "vector": [0, 0, 3],
            },
            {"id": "shell", "op": "cut", "inputs": ["outer", "inner_pos"]},
            {"id": "drain", "op": "cylinder", "radius": 3, "height": 5},
            {"id": "result", "op": "cut", "inputs": ["shell", "drain"]},
        ]
        name = "Desktop planter"
    else:
        operations = [
            {"id": "outer", "op": "box", "size": [100, 65, 30]},
            {
                "id": "rounded",
                "op": "fillet",
                "inputs": ["outer"],
                "radius": 5,
                "edges": "|Z",
            },
            {"id": "inner", "op": "box", "size": [94, 59, 30]},
            {
                "id": "inner_round",
                "op": "fillet",
                "inputs": ["inner"],
                "radius": 3,
                "edges": "|Z",
            },
            {
                "id": "pocket",
                "op": "translate",
                "inputs": ["inner_round"],
                "vector": [0, 0, 3],
            },
            {"id": "shell", "op": "cut", "inputs": ["rounded", "pocket"]},
            {"id": "divider", "op": "box", "size": [3, 60, 27]},
            {
                "id": "divider_pos",
                "op": "translate",
                "inputs": ["divider"],
                "vector": [12, 0, 0],
            },
            {"id": "result", "op": "union", "inputs": ["shell", "divider_pos"]},
        ]
        name = "Pocket organizer"
    return CADPlan(
        name=name,
        description="A real CAD model built from a predefined demo template.",
        assumptions=[
            "Demo mode uses fixed dimensions, not your prompt.",
            "All dimensions are in millimeters.",
        ],
        operations=operations,
        result="result",
    )
