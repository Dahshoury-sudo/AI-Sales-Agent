SCENARIOS = [
  {
    "id": "CONV1005",
    "category": "regression",
    "persona": "ready_buyer",
    "turns": [
      "عايز برفان ثابت للجيم وفريش",
      "رجالي",
      "بكام ده",
      "في اي تاني طيب يكون 90 ملي في حدود 900 جنيه",
      "دول ينفعو للجيم ؟",
      "قولي النوتات",
      "والتاني ؟",
      "انهي فيهم مسكر اكتر ؟",
      "قولتلي بكام",
      "ماشي هات 90 ملي من اول واحد ده",
      "محمد فؤاد 01153032052 01051089101 المقطم شارع 9 جنب فاميلي درينك",
      "مش عايز 1 × Noirvel (90ml)",
      "عايز Le Male (90ml)",
      "محمد فؤاد 01153032052 01051089101 المقطم شارع 9 جنب فاميلي درينك",
      "تمام"
    ],
    "probe": "Replay of conversation 1005. Two failures at checkout: \"ماشي هات 90 ملي من اول واحد ده\" put BOTH perfumes in the cart (the ordinal means one — Le Male, listed first) including Noirvel 90ml at 1085 against a stated 900 budget; and \"مش عايز 1 × Noirvel (90ml)\" — a removal of one line out of two — cancelled the whole order and erased the name, phone and address, forcing the customer to retype everything. Removing one item must EDIT the order, keep the other perfume and keep the contact details."
  }
]
