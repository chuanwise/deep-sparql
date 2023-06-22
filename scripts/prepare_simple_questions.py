import argparse
import os

from tqdm import tqdm


from deep_sparql.utils import (
    load_str_index,
    load_id_index,
)


from text_correction_utils.io import load_text_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=str, required=True)
    parser.add_argument("--input", type=str, required=True)
    parser.add_argument("--bracket-begin", type=str, default="<bob>")
    parser.add_argument("--bracket-end", type=str, default="<eob>")
    parser.add_argument("--entity-begin", type=str, default="<boe>")
    parser.add_argument("--entity-end", type=str, default="<eoe>")
    parser.add_argument("--property-begin", type=str, default="<bop>")
    parser.add_argument("--property-end", type=str, default="<eop>")
    parser.add_argument("--var-begin", type=str, default="<bov>")
    parser.add_argument("--var-end", type=str, default="<eov>")
    parser.add_argument("--target", type=str, required=True)
    parser.add_argument("--entity-index", type=str, required=True)
    parser.add_argument("--property-index", type=str, required=True)
    parser.add_argument("--inverse-index", type=str, required=True)
    return parser.parse_args()


def surround(s: str, op: str, cl: str) -> str:
    return f"{op}{s}{cl}"


def prepare(args: argparse.Namespace):
    entity_index = load_str_index(args.entity_index)
    property_index = load_str_index(args.property_index)
    inverse_index = load_id_index(args.inverse_index)

    os.makedirs(os.path.dirname(args.input), exist_ok=True)
    os.makedirs(os.path.dirname(args.target), exist_ok=True)

    lines = load_text_file(args.data)
    with open(args.input, "w") as of, \
            open(args.target, "w") as tf:
        for line in tqdm(
            lines,
            desc="preparing simple questions",
            leave=False
        ):
            subj, prop, obj, question = line.split("\t")
            if prop.startswith("R"):
                subj, obj = obj, subj
                subj = "x"
                prop = "P" + prop[1:]
            else:
                obj = "x"
                prop = "P" + prop[1:]

            if subj != "x":
                subj_id = int(subj[1:])
                if subj_id not in entity_index:
                    continue
                subjs = [
                    surround(subj, args.entity_begin, args.entity_end)
                    for subj in entity_index[subj_id]
                ]
                objs = [surround(obj, args.var_begin, args.var_end)]
            else:
                obj_id = int(obj[1:])
                if obj_id not in entity_index:
                    continue
                objs = [
                    surround(obj, args.entity_begin, args.entity_end)
                    for obj in entity_index[obj_id]
                ]
                subjs = [surround(subj, args.var_begin, args.var_end)]

            prop_id = int(prop[1:])
            assert prop_id in property_index
            props = [
                surround(prop, args.property_begin, args.property_end)
                for prop in property_index[prop_id]
            ]

            var = surround('x', args.var_begin, args.var_end)

            for subj in subjs:
                for prop in props:
                    for obj in objs:
                        of.write(f"{question}\n")
                        tf.write(
                            f"SELECT {var} "
                            f"WHERE {args.bracket_begin} "
                            f"{subj} {prop} {obj} . "
                            f"{args.bracket_end}\n"
                        )


if __name__ == "__main__":
    prepare(parse_args())
