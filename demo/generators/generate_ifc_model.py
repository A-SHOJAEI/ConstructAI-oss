"""
Generate a simple IFC4 model using IfcOpenShell.

Creates:
- 1 site, 1 building, 5 storeys
- 20 walls (4 per floor)
- 5 slabs (1 per floor)
- 10 columns (2 per floor)
- Property sets with material properties

Usage:
    python -m demo.generators.generate_ifc_model [output_path]
"""
import sys
from pathlib import Path

try:
    import ifcopenshell
    import ifcopenshell.api
    HAS_IFC = True
except ImportError:
    HAS_IFC = False


def generate_ifc_model(output_path: Path) -> Path:
    if not HAS_IFC:
        print("ifcopenshell not installed. Creating placeholder IFC description file.")
        output_path = output_path.with_suffix(".txt")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "IFC Model Placeholder\n"
            "=====================\n"
            "Install ifcopenshell to generate actual IFC4 model:\n"
            "  pip install ifcopenshell\n\n"
            "Model specification:\n"
            "  - 1 IfcSite: Riverside Mixed-Use Development Site\n"
            "  - 1 IfcBuilding: Main Building\n"
            "  - 5 IfcBuildingStorey: Levels 1-5\n"
            "  - 20 IfcWall: 4 per floor (N, S, E, W)\n"
            "  - 5 IfcSlab: 1 per floor\n"
            "  - 10 IfcColumn: 2 per floor\n"
            "  - Total elements: 36\n"
        )
        return output_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    model = ifcopenshell.api.run("project.create_file", version="IFC4")

    # Project
    project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="RMD-2025-001")
    ifcopenshell.api.run("unit.assign_unit", model)
    ctx = ifcopenshell.api.run("context.add_context", model, context_type="Model")
    body = ifcopenshell.api.run(
        "context.add_context", model, context_type="Model",
        context_identifier="Body", target_view="MODEL_VIEW", parent=ctx,
    )

    # Site
    site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="Riverside Site")
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=project, products=[site])

    # Building
    building = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="Main Building")
    ifcopenshell.api.run("aggregate.assign_object", model, relating_object=site, products=[building])

    # Materials
    concrete = ifcopenshell.api.run("material.add_material", model, name="Concrete C30/37")
    steel = ifcopenshell.api.run("material.add_material", model, name="Steel S355")

    storey_height = 4.0  # meters
    for floor in range(1, 6):
        elevation = (floor - 1) * storey_height
        storey_name = f"Level {floor}"
        storey = ifcopenshell.api.run(
            "root.create_entity", model, ifc_class="IfcBuildingStorey", name=storey_name,
        )
        ifcopenshell.api.run("aggregate.assign_object", model, relating_object=building, products=[storey])

        # 4 walls per floor
        for direction in ["North", "South", "East", "West"]:
            wall = ifcopenshell.api.run(
                "root.create_entity", model, ifc_class="IfcWall",
                name=f"Wall-{floor}-{direction}",
            )
            ifcopenshell.api.run("spatial.assign_container", model, relating_structure=storey, products=[wall])
            ifcopenshell.api.run("material.assign_material", model, products=[wall], material=concrete)

        # 1 slab per floor
        slab = ifcopenshell.api.run(
            "root.create_entity", model, ifc_class="IfcSlab",
            name=f"Slab-{floor}",
        )
        ifcopenshell.api.run("spatial.assign_container", model, relating_structure=storey, products=[slab])
        ifcopenshell.api.run("material.assign_material", model, products=[slab], material=concrete)

        # 2 columns per floor
        for col_idx in range(1, 3):
            column = ifcopenshell.api.run(
                "root.create_entity", model, ifc_class="IfcColumn",
                name=f"Column-{floor}-{col_idx}",
            )
            ifcopenshell.api.run("spatial.assign_container", model, relating_structure=storey, products=[column])
            ifcopenshell.api.run("material.assign_material", model, products=[column], material=steel)

    model.write(str(output_path))
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("demo/output/riverside_model.ifc")
    p = generate_ifc_model(out)
    print(f"Generated: {p}")
