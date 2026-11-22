# Encumbrance

**Categories:** Gameplay · **Source:** [Fandom](https://thelongdark.fandom.com/wiki/Encumbrance)

**Contents**

- [Mechanics](#mechanics)
- [Baseline Capacity and Increasing Carry Capacity](#baseline-capacity-and-increasing-carry-capacity)
- [Decreasing Carrying Capacity](#decreasing-carrying-capacity)
- [Movement Thresholds](#movement-thresholds)
- [Carry Weight Over-Encumbrance](#carry-weight-over-encumbrance)
- [Wind and Terrain](#wind-and-terrain)
- [Travois](#travois)
- [Strategy](#strategy)
- [Character quotes](#character-quotes)

![Weight icon](../assets/Weight_icon.png) **Encumbrance** is a player statistic that represents the total amount of weight the player can carry and how it effects their movement speed in the world.

Players can see the total weight of the items they are carrying on the Backpack screen from the Status [Survival Menu](./Survival%20Menu.md). This will include items they have equipped as well as those carried in the inventory. The Backpack screen truncates the amount of weight to two decimal places.

## Mechanics

### Baseline Capacity and Increasing Carry Capacity

The player's normal carrying capacity starts at 30 kg (66 lb). This baseline can be increased by:

| Image | Name | Carrying capacity increase | Requirments | Expires |
| --- | --- | --- | --- | --- |
| [Moose Hide Satchel icon](../assets/Moose-Hide_Satchel_icon.png) | [Moose-Hide Satchel](./Moose-Hide%20Satchel.md) | 5.00 kg<br>_(11 lb)_ | Wear Satchel | Satchel is taken off or ruined |
| [Well Fed icon](../assets/Well_Fed_icon.png) | [Well Fed](./Well%20Fed.md) | 5.00 kg<br>_(11 lb)_ | Avoid starvation for 72 hours | When Calorie Store reaches 0 |
| [Technical Backpack icon](../assets/Technical_Backpack_icon.png) | [Technical Backpack](./Technical%20Backpack.md) | 5.00 kg<br>_(11 lb)_ | Acquire Backpack | Never |
| [Unburdened icon](../assets/Unburdened_icon.png) | [Unburdened](./Unburdened.md) | 3.00 kg<br>_(6.6 lb)_ | Consume: [Ptarmigan Pie icon](../assets/Ptarmigan_Pie_icon.png) [Ptarmigan Pie](./Ptarmigan%20Pie.md)[Rabbit Pie icon](../assets/Rabbit_Pie_icon.png) [Rabbit Pie](./Rabbit%20Pie.md)[Venison Pie icon](../assets/Venison_Pie_icon.png) [Venison Pie](./Venison%20Pie.md)[Porter's Soup icon](../assets/Porter%27s_Soup_icon.png) [Porter's Soup](./Porter%27s%20Soup.md)[Pemmican Bar icon](../assets/Pemmican_Bar_icon.png) [Pemmican Bar](./Pemmican%20Bar.md) | 3 hours |
| [Travois concept](../assets/Travois_concept.jpg) | [Travois](./Travois.md) | 75.00 kg<br>_(165 lb)_ | Carry Travois | When put down |

These effects can be stacked, meaning that the player can increase their normal carrying capacity by up to 15 kg (33 lb). However, since there is a limit to how many satchels/backpacks the player can equip (only one of each can be equipped at a time), 45 kg (99 lb) is the highest-possible carrying capacity in the base game.

If [Tales from the Far Territory](./Tales%20from%20the%20Far%20Territory.md) has been purchased, players can temporarily increase their normal carrying capacity by an extra 3 kg by achieving the [Unburdened](./Unburdened.md) Affliction, gained by eating [Ptarmigan Pie](./Ptarmigan%20Pie.md), [Rabbit Pie](./Rabbit%20Pie.md), or [Venison Pie](./Venison%20Pie.md). Unburdened only lasts for 3 hours but if all other ways to increase max carry weight are acquired, this extra 3 kg creates the highest-possible carrying capacity in-game at 48 kg (105 lb).

Using a [Travois](./Travois.md) does not directly increase a player's carry weight, but it's capacity to carry up to 75 kg while also being dragged behind the player is a notable addition to the carry weight formula. Keep in mind that more weight on a Travois will slow the player down.

### Decreasing Carrying Capacity

The amount of weight the character is carrying increases the rate of [Fatigue](./Fatigue.md). When Fatigue reaches 50%, the max carrying capacity will begin to decrease, slowly at first but the rate will rapidly increase as your Fatigue gets closer to 0%. Encumbrance penalties apply as normal as a survivor becomes more drained. Once the 'Exhausted' Fatigue state is reached, the max carry limit will be half the normal value (30 kg becomes 15 kg). The fatigue meter is primarily restored with [Sleep](./Sleep.md), and, once the player has regained at least 50% of their fatigue, (equivalent to 6 hours of sleep) the maximum carrying capacity is restored. [Coffee](./Cup%20of%20Coffee.md) may also offer minor relief from a low fatigue meter.

[Broken Ribs](./Broken%20Ribs.md) from a [Moose](./Moose.md) [Struggle](./Struggle.md) will remove 25% of maximum carrying capacity until healed. [Scurvy](./Scurvy.md) will also weaken a survivor to the point where they will lose 5 kg of carrying capacity until healed. If the [Burdened](./Burdened.md) Affliction is gained, then max carry capacity will be reduced by 3 kg for 1 hour.

| Image | Name | Carrying capacity increase |
| --- | --- | --- |
| [Fatigue Reduced icon](../assets/Fatigue_Reduced_icon.png) | Low [Fatigue](./Fatigue.md) | -0.01 kg to -15.00 kg<br>_(0.02 - 33.09 lbs)_ |
| [Burdened icon](../assets/Burdened_icon.png) | [Burdened](./Burdened.md) | -3.00 kg (-6.6 lb) for 1 hour |
| [Broken Ribs icon](../assets/Broken_Ribs_icon.png) | [Broken Ribs](./Broken%20Ribs.md) | 25% reduction until healed |
| [Scurvy icon](../assets/Scurvy_icon.png) | [Scurvy](./Scurvy.md) | -5 kg (-11 lb) |
| [Diminished Form icon](../assets/Diminished_Form_icon.png) | [Diminished Form](./Diminished%20Form.md) | permanent -5 kg (-11 lb) |

## Movement Thresholds

### Carry Weight Over-Encumbrance

Detrimental effects to movement occur gradually relative to the amount of weight over capacity. There are certain thresholds that heavily affect movement speed, for example a player with exactly 9.99 kg (22.02 lbs) over capacity can still sprint, but a player with 10.00 kg over capacity cannot. Being more than 29.5 kg over capacity makes the player unable to move. Being aware of these thresholds is important to properly select the amount of loot you can carry.

The baseline Carrying Weight for this example starts at 30/30kg. For every 1kg over Carry Weight, -3.3% movement penalty is applied.

| Carrying Capacity | Movement Penalty | Effect |
| --- | --- | --- |
| 30/30 kg | 0% | The maximum weight a player can carry without penalty. / The maximum weight for [Rope Climbing](./Rope%20Climbing.md). |
| 31/30 kg | -3.3% | Slight slowdown. Player can no longer Rope Climb. / [Sprain Risk](./Sprain%20Risk.md) appears. |
| 40/30 kg | -33.3% | The player can no longer [Sprint](./Stamina.md). |
| 50/30 kg | -66.6% | The player's walking speed is significantly decreased to a crawl. |
| 60/30 kg | -100% | The player is completely immobile, and can no longer walk. |

### Wind and Terrain

Harsh winds and the terrain directly effect a player's movement as they traverse the world, slowing or speeding the player when encountered. Note that the Celestial Navigator [Feat](./Feats.md) adds a 10% bonus walking speed during a clear night or aurora.

Do note that light winds currently slow down the player more than heavy winds do, likely due to a bug.

| Terrain | Max Bonus | Max Penalty |
| --- | --- | --- |
| Roads[¹](#fn1) | 15% | 0% |
| Slopes | -100% | -20% |
| Heavy Wind[²](#fn2) | 20% | 50% |
| Indoors[³](#fn3) | 0% (Outside) | 33% (Inside) |
| Blizzard[⁴](#fn4) | 0% | 0% |
| Crab Walk[⁵](#fn5) | 3-21% | 0 |
| Sprinting | 45% | 0 |

### Travois

The [Travois](./Travois.md) will apply a penalty to the player's movement speed when dragged behind them. This penalty increases as more items are added to the satchel. Putting items into the Travois instead of carrying them on player's person is always the better choice, as it does not slow the player down as much.

| Maximum weight | Movement Penalty |
| --- | --- |
| 0/75 kilograms | 0% |
| 25/75 kilograms | -4.9% |
| 50/75 kilograms | -19.2% |
| 75/75 kilograms | -50.3% |

## Strategy

It is very easy to exceed your carry capacity in The Long Dark, especially on more loot-rich game modes such as Voyageur or Pilgrim. A well equipped character carrying minimum reserves of food and firewood, a basic assortment of tools and a weapon will almost automatically exceed the base capacity. It is hence not always desirable to stay within capacity. Especially when transporting greater amounts of loot, like after successfully hunting an animal, or when moving acquired loot out of a region, exceeding capacity often cannot be avoided.

However, aware of this limitation, a player should carefully consider what equipment to bring, and what to leave behind at a shelter. This especially includes tools and weapons, as those usually weigh in very heavily, but also food and firewood. Even the selection of clothing can be put to question. Balancing the amount of equipment you deem useful against your desired mobility is a key strategic element of The Long Dark.

- Within carry capacity: desirable, but with good equipment can be hard to achieve without adding to it with the Well Fed buff or the Moose-Hide Satchel.
- Up to 5 kg (11 lbs) above capacity: adequate if [Rope Climbing](./Rope%20Climbing.md) is not on the player's route as the slow down is negligible. It is advisable to stay within this limit for tasks that require substantial mobility and carry reserves, such as exploring and looting part of a region.
- 5 kg (11 lbs) to 10 kg (22 lbs) above capacity: generally acceptable as the amount of slow down is moderate, chances for sprains are still within reason, and sprinting for emergencies is still available. This is the sane maximum of equipment a player should bring for any task other than reallocating loot to a different shelter.
- 10 kg (22 lbs) to 15 kg (33 lbs) above capacity: still within reason for hauling loot over relatively safe and open terrain, but the inability to sprint must be kept in mind to properly alter behavior towards predators.
- 15 kg (33 lbs) to 20 kg (44 lbs) above capacity: generally inadvisable as the slow down is very pronounced, and even [bears](./Bear.md) can no longer be escaped. At this point it is probably faster and definitively safer to take a route twice with considerably less weight.
- More than 20 kg (44 lbs) above capacity: the player is so slow that dropping excess loot becomes inevitable. Highly dangerous, and in any case inefficient.

## Character quotes

- "This pack is gettin' kinda heavy!"
- "This pack is getting too heavy to carry!"
- "I might have to drop something."
- "Not sure I can carry much more."
- "MMMGH, somethin's gotta go."
- "Feels like a lot of gear."
- "I'm not going to be able to carry this load for much longer."

Totally encumbered

- "Too heavy, can't move."
- "TOO, MUCH, TO CARRY!!!"

## Notes

---

1. Roads provide 50% walking speed boost compared to snow, concrete, or metal terrain.[↩︎](#fnref1)
2. Max Bonus &amp; Penalty for Heavy Wind are applied when walking with or against oncoming winds respectively.[↩︎](#fnref2)
3. Stepping indoors (Back of a [Warm Cave](./Warm%20Cave.md) or building interiors) slows the player down instantly[↩︎](#fnref3)
4. Blizzards do not slow the player down despite the raging windstorm, likely for balancing reasons.[↩︎](#fnref4)
5. Moving diagonally at a 45° angle increases movement by 3%, 50° angle by 21% (controller only), and using a controller emulator to hit both angles increases speeds by 42%.[↩︎](#fnref5)
