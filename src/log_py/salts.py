SALT_ALIASES = {
    "": ["unknown", "skip"],
    "citrate": ["citric"],
    "ascorbate": ["vit-c"],
    "fumarate": ["fum"],
    "freebase": ["fb", "base"],
    "hydrobromide": ["hbr"],
    "hydrochloride": ["hcl"],
    "malate": ["mal"],
    "mesylate": ["mes"],
    "phosphate": ["phos"],
    "succinate": ["succ"],
    "sulfate": ["sulphate", "sulf"],
    "tartrate": ["tart", "tar"],

    "iminodiacetic acid": [],
    "sodium": [],
    "potassium": [],
    # disodium dihydrate
    "borate": [],
    "besylate": [],
    "carbonate": [],
    "bicarbonate": [],
    "arsenate": [],
    # arsenate dihydrate
    "nitrate": [],
    "acetate": [],
    #"hydrochloride": [],
    # hydrochloride dihydrate
    # hydrocloride
    #"hydrobromide": [],
    "hydriodide": [],
    #"phosphate": [],
    # phosphate dihydrate
    #"tartrate": [],
    "bitartrate": [],
    "monohydrate": [],
    "dihydrate": [],
    "chlorphenoxyacetate": [],
    #"malate": [],
    "maleate": [],
    #"citrate": [],
    #"mesylate": [],
    "dimesylate": [],
    "camsilate": [],
    "tosylate": [],
    "adipate": [],
    "aspartate": [],
    "saccharate": [],
    #"succinate": [],
    #"fumarate": [],
    "valerate": [],
    "cypionate": [],
    "enantate": [],
    "undecylate": [],
    "propionate": [],
    "oxalate": [],
    "salicylate": [],
    "hypophosphite": [],
    # hypophosphite dihydrate
    #"sulfate": [],
    "laurylsulfate": [],
    "hemisulfate": [],
    # sulfate monohydrate
    # sulfate pentahydrate
    # sulphate
    # laurylsulphate
    # hemisulphate
    # sulphate monohydrate
    # sulphate pentahydrate
    "hemihydrate": [],
}
SALT_CHOICES = [
    "Skip",
    "Neutral",
    "Acid",
    "freebase",
    "hydrochloride",
    "sulfate",
    *[
        salt
        for salt in SALT_ALIASES
        if salt not in {"freebase", "hydrochloride", "sulfate"}
    ],
]
