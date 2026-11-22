# Fatigue

**Categories:** Gameplay · **Source:** [Fandom](https://thelongdark.fandom.com/wiki/Fatigue)

**Contents**

- [Gaining/Losing Fatigue](#gaininglosing-fatigue)
- [Sleep](#sleep)
- [Food items](#food-items)
- [Food](#food)
- [Fatigue Increase:](#fatigue-increase)
- [Fatigue Decrease:](#fatigue-decrease)
- [Affecting Condition](#affecting-condition)
- [Other Effects](#other-effects)
- [Sprinting](#sprinting)
- [Rope climbing](#rope-climbing)
- [Carry Weight](#carry-weight)
- [Weapons](#weapons)
- [Struggles](#struggles)
- [Starvation](#starvation)
- [Fatigue Bar](#fatigue-bar)
- [Exhausted](#exhausted)
- [Drained](#drained)
- [Tired](#tired)
- [Winded](#winded)
- [Rested](#rested)
- [Hunger and Parasites affects fatigue](#hunger-and-parasites-affects-fatigue)
- [Character quotes](#character-quotes)
- [Trivia](#trivia)

![Fatigue icon](../assets/Fatigue_icon.png) **Fatigue** is one of the four essential [Needs](./Needs.md). Fatigue is the measure of how tired the character is. Fatigue constantly drains but can be replenished by sleeping. As the character tires, Fatigue begins to affect the ability to move, aim weapons, and [struggles](./Struggle.md). When completely depleted, fatigue will begin to lower the player's [Condition](./Condition.md).

<span id="gaininglosing_fatigue"></span><span id="Gaining/Losing_Fatigue"></span><span id="gaining/losing_fatigue"></span><span id="gaininglosing-fatigue"></span>

## Gaining/Losing Fatigue

As long as the character is awake, fatigue will constantly be emptying. The rate of drop is directly dependent on activity level and encumbrance but may also be influenced by other factors like [temperature](./Feels%20Like.md) and [illness](./Afflictions.md). Example: The fatigue bar can be emptied as quickly as in 2.6 game hours by sprinting as often as possible, while a long [Rope Climbing](./Rope%20Climbing.md) session can easily consume half the player's fatigue in minutes. However, remaining inactive by passing time at a [bedroll](./Bedroll.md) will see the same bar last almost 22 game hours.

Properly balancing one's activity level somewhere between "always resting" and "always sprinting" should see most players make it through a 12 hour activity period before reaching exhaustion, provided that they're not exploring in an area full of climbing ropes.

| Activity | Fatigue Loss (per realtime minute) | Notes |
| --- | --- | --- |
| Standing | 1% |  |
| Walking | 2% |  |
| Sprinting | 15%-30% | Increases linearly based on how long you have been sprinting uninterrupted. |
| Rope Climbing Up | 60% |  |
| Rope Climbing Down/Holding | 30% |  |

In custom games, non-standard game timescales will multiply normal sources of fatigue (standing/walking/sprinting/climbing) relative to real-time. For example, in a custom game with 4x day length, you will only expend 0.5% stamina per real-time minute while walking.

<span id="sleep"></span><span id="Sleep"></span>

### Sleep

[Sleep](./Sleep.md) is the most common method of satisfying fatigue. It takes 12 full hours of sleep to fully replenish an empty fatigue bar. Or 1/12th of the bar will fill for each hour slept. The player will wake automatically when the bar has been completely refilled.

<span id="food_items"></span><span id="Food_items"></span><span id="food-items"></span>

### Food items

[Cup of Coffee](./Cup%20of%20Coffee.md) can be used to help ward off tiredness. When consumed, it fills 10% of the fatigue bar. [Cup of Acorn Coffee](./Cup%20of%20Acorn%20Coffee.md) has the same effect and fills 5% of the fatigue bar. Both confers the [Fatigue Reduced](./Fatigue%20Reduced.md) affliction, which slows the rate at which fatigue empties.

[Emergency Stim](./Emergency%20Stim.md) immediately boosts the fatigue bar to full and keeps it there until the effect wears off, where upon Fatigue immediately drops to 0.

Drinking a [Go! Energy Drink](./Go!%20Energy%20Drink.md) will immediately refill the player's [Stamina](./Stamina.md) and half the player's fatigue for a total time of 45 in-game minutes. After 30 minutes, the player will get a [Headache](./Headache.md), and at the end of the 45 minutes, the player's fatigue will drop by 50%.

| Item | Initial Reduction | Fatigue Loss | Duration |
| --- | --- | --- | --- |
| [Cup of Coffee icon](../assets/Cup_of_Coffee_icon.png) [Cup of Coffee](./Cup%20of%20Coffee.md) | 10% | 50% | 60 minutes |
| [Cup of Acorn Coffee icon](../assets/Cup_of_Acorn_Coffee_icon.png) [Cup of Acorn Coffee](./Cup%20of%20Acorn%20Coffee.md) | 5% | 75% | 30 minutes |
| [Go! Energy Drink icon](../assets/Go!_Energy_Drink_icon.png) [Go! Energy Drink](./Go!%20Energy%20Drink.md) | 20% | 50% | 45 minutes |
| [Emergency Stim icon](../assets/Emergency_Stim_icon.png) [Emergency Stim](./Emergency%20Stim.md) | 10% | 50% | 60 minutes |

<span id="food"></span><span id="Food"></span>

### Food

Eating certain foods can also affect the fatigue meter.

<span id="fatigue_increase"></span><span id="Fatigue_Increase:"></span><span id="fatigue_increase:"></span><span id="fatigue-increase"></span>

#### Fatigue Increase:

| Item | Name | Initial Increase |
| --- | --- | --- |
| [Breyerhouse Pie icon](../assets/Breyerhouse_Pie_icon.png) | [Breyerhouse Pie](./Breyerhouse%20Pie.md) | 15% |
| [Rose Hip Pie icon](../assets/Rose_Hip_Pie_icon.png) | [Rose Hip Pie](./Rose%20Hip%20Pie.md) | 15% |
| [Peach Pie icon](../assets/Peach_Pie_icon.png) | [Peach Pie](./Peach%20Pie.md) | 15% |

<span id="fatigue_decrease"></span><span id="Fatigue_Decrease:"></span><span id="fatigue_decrease:"></span><span id="fatigue-decrease"></span>

#### Fatigue Decrease:

| Item | Name | Initial Decrease |
| --- | --- | --- |
| [Ptarmigan Stew icon](../assets/Ptarmigan_Stew_icon.png) | [Ptarmigan Stew](./Ptarmigan%20Stew.md) | -15% |
| [Rabbit Stew icon](../assets/Rabbit_Stew_icon.png) | [Rabbit Stew](./Rabbit%20Stew.md) | -15% |
| [Trout Stew icon](../assets/Trout_Stew_icon.png) | [Trout Stew](./Trout%20Stew.md) | -15% |
| [Venison Stew icon](../assets/Venison_Stew_icon.png) | [Venison Stew](./Venison%20Stew.md) | -15% |
| [Ranger Stew icon](../assets/Ranger_Stew_icon.png) | [Ranger Stew](./Ranger%20Stew.md) | -15% |
| [Thomson Family Stew icon](../assets/Thomson_Family_Stew_icon.png) | [Thomson Family Stew](./Thomson%20Family%20Stew.md) | -15% |

<span id="affecting_condition"></span><span id="Affecting_Condition"></span><span id="affecting-condition"></span>

## Affecting Condition

When the fatigue bar is empty, condition begins to drain. The rate of drain is fixed at 1% per hour (See: [Needs](./Needs.md)). The slowness of this drain has lead some players to take advantage, accepting minor condition losses during strenuous activities in a practice known as [Hibernation](./Hibernation.md).

<span id="other_effects"></span><span id="Other_Effects"></span><span id="other-effects"></span>

## Other Effects

<span id="sprinting"></span><span id="Sprinting"></span>

### Sprinting

Sprinting contributes significantly to rate of fatigue. In turn, fatigue affects the replenishment of [stamina](./Stamina.md) which enables sprinting. When the character reaches Exhausted (empty fatigue bar), sprinting becomes impossible. It will remain disabled for as long as the fatigue bar remains empty.

<span id="rope_climbing"></span><span id="Rope_climbing"></span><span id="rope-climbing"></span>

### Rope climbing

Rope climbing is much faster when the player is well-rested. Climbing up a rope is the most strenuous activity in-game, emptying both fatigue and stamina very quickly. When the player becomes exhausted, it is not possible to start a new climb, and climbing will become very slow. The rate of stamina decrease, however, remains constant, increasing the risk of slipping and falling.

<span id="carry_weight"></span><span id="Carry_Weight"></span><span id="carry-weight"></span>

### Carry Weight

The amount of weight the character is carrying increases the rate of fatigue. Also, as the fatigue bar reaches below 50%, the 'recommended encumbrance' will begin to decrease. Encumbrance penalties apply as normal (i.e if your total carried weight is greater than your 'recommended encumbrance', too much more and you cannot sprint, etc). Once the 'Exhausted' fatigue state is reached, 'recommended encumbrance' will become half the normal value (or 15kg). This can be fixed with adequate rest.

<span id="weapons"></span><span id="Weapons"></span>

### Weapons

The ability to hold a weapon steady while aiming is affected by fatigue. This can be significant for using the [Hunting Rifle](./Hunting%20Rifle.md), [Survival Bow](./Survival%20Bow.md), or [Bear Spear](./Bear%20Spear.md). Aim will waiver more and the Bear Spear cannot be set for very long when the fatigue bar is low. (See:[Hunting](./Hunting.md)).

<span id="struggles"></span><span id="Struggles"></span>

### Struggles

When the character is fully rested, struggles seem to take less time and the [struggle](./Struggle.md) bar seems more easily filled. As exhaustion sets in, the opposite becomes apparent. Great difficulty is imparted and the struggle bar may become impossible to move. Being attacked by Animals during exhaustion is extremely hazardous. It's worth noting fatigue has no direct disadvantage in a [Moose](./Moose.md), or [Bear](./Bear.md) mauling, due to the inability to fight while being mauled.

<span id="starvation"></span><span id="Starvation"></span>

### Starvation

If a player remains starving for a long enough time, the maximal amount of rest that they can acquire will start to gradually decrease. It is noticed by the yellow exclamation mark that appears beside the eye icon and by the red color that starts filling up the inside of the fatigue bar. This issue is easily solved by consuming food, the red color inside the fatigue bar will then gradually start to decrease, but that doesn't prevent the player from entirely filling up their fatigue bar with sleep right away.

<span id="fatigue_bar"></span><span id="Fatigue_Bar"></span><span id="fatigue-bar"></span>

## Fatigue Bar

Fatigue bar progresses from Exhausted ↔︎ Drained ↔︎ Tired ↔︎ Winded ↔︎ Rested. Arrows in the bar indicate which direction it is filling with more arrows meaning faster progress. Fatigue is the inverse of the fatigue bar, meaning that when the bar is full, fatigue is at a minimum, and when it is depleted, fatigue is at a maximum.

<span id="exhausted"></span><span id="Exhausted"></span>

### Exhausted

- Constant Condition drain
- Cannot sprint
- Recommended encumbrance reduced to half normal (15kg/33 lbs)
- Significant difficulties aiming bow/rifle
- Severe penalties during struggles
- Rope climbing is slow and falls become extremely likely

<span id="drained"></span><span id="Drained"></span>

### Drained

- Noticeable difficulty aiming bow/rifle
- Significant penalties during struggles

<span id="tired"></span><span id="Tired"></span>

### Tired

- Recommended encumbrance begins to drop when the fatigue bar reaches below 50% in all difficulties
- Rope climbing at average rate of speed

<span id="winded"></span><span id="Winded"></span>

### Winded

<span id="rested"></span><span id="Rested"></span>

### Rested

- Sprinting seems enhanced (faster/less stamina drain)
- Bow seems steady during aiming
- Struggles seem easier (except against bears)
- Rope climbing is easy and fast

<span id="hunger_and_parasites_affects_fatigue"></span><span id="Hunger_and_Parasites_affects_fatigue"></span><span id="hunger-and-parasites-affects-fatigue"></span>

## Hunger and Parasites affects fatigue

![UI Long Hunger](../assets/UI_Long-Hunger.jpg) A player that remains starving for a long enough time will start to have their [fatigue](./Fatigue.md) affected; the maximal amount of energy that the player will be able to acquire through sleep will start to decrease, which is represented by the red color that starts filling up the inside of the fatigue bar, as well as by the yellow exclamation mark that appears next to the eye icon, symbolizing the fatigue bar. This issue is easily solved by consuming food, the red color won't vanish immediately, but the player will be allowed to gain full energy from sleep right away.

[Intestinal Parasites](./Intestinal%20Parasites.md) also slowly drain the player's maximum available Fatigue bar to the same effect, but have a far harsher treating period.

<span id="character_quotes"></span><span id="Character_quotes"></span><span id="character-quotes"></span>

## Character quotes

- "Not sure why I feel so tired."
- "My energy is just...disappearing."
- "Just need to close my eyes..."
- "I'm gonna need to find a place to rest soon."
- "Something is sapping my energy."
- "If I don't rest soon I'm gonna faint."
- "I could go for a nap right about now."
- "(Yawns) I'd lay down right here if I thought I'd ever wake up again." (Male Survivor)
- "So tired I could lay down right here." (Female Survivor)
- "Think I need to lay down."
- "Gonna need to rest soon"
- (Tired exhausted sighing sounds)
- (Deep and heavy inhaling and exhaling)
- (Yawning sounds)

<span id="trivia"></span><span id="Trivia"></span>

## Trivia
