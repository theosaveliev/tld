# Cabin Fever Risk

**Categories:** Afflictions, Pages with update history · **Source:** [Fandom](https://thelongdark.fandom.com/wiki/Cabin_Fever_Risk)

**Contents**

- [Overview](#overview)
- [Special Locations](#special-locations)
- [Treatment](#treatment)
- [Update history](#update-history)

**Cabin Fever Risk**

| Property | Details |
| --- | --- |
|  | [Cabin Fever Risk icon](../assets/Cabin_Fever_Risk_icon.png) |
| Release | [25 May 2016](./The%20Long%20Dark%20%E2%80%94%20Early%20Access%20Changelog.md#0.338) |
| Update | 0.338 |
| **Properties** |  |
| Category | [Afflictions](./Afflictions.md) |
| Description | You've been indoors so long you are starting to feel very uneasy. Spend some time outdoors before it gets worse. |
| Caused By | Spending too much time indoors |
| Effect | Negative |
| Type | Risk |
| Affliction Duration | Until Treated |
| **Treatment** |  |
| Treatment | Spend time outdoors |
| Recovery Time | Immediate |
| **Advanced Data** |  |
| Item Key | GAMEPLAY\_CabinFeverRisk |

> “**You've been indoors so long you are starting to feel very uneasy. Spend some time outdoors before it gets worse!**”
>
> — In-game description

**Cabin Fever Risk** is an [Affliction](./Afflictions.md) that is caused by spending time indoors. This Affliction only appears when playing on the [Voyageur](./Voyageur.md), [Stalker](./Stalker.md), [Interloper](./Interloper.md), [Misery](./Misery.md), or enabled on [Custom](./Custom.md) experiences in [Survival Mode](./Survival%20Mode.md), or Hardened Survivor in [Wintermute](./Wintermute.md).

Starting a Survival Mode game with the Settled Mind [Feat](./Feats.md) selected will automatically prevent Cabin Fever Risk, and [Cabin Fever](./Cabin%20Fever.md), from ever developing.

## Overview

The Affliction Risk for Cabin Fever is calculated by the amount of time a survivor spends indoors in the last 150 hours:

- Spending 113 hours out of the past 150 hours (6.25 days) indoors begins the Risk. This averages ~18 hours/day, or 75% of your time.
- This affliction is removed when this number drops below 110 out of the past 150 hours, or is replaced by the affliction [Cabin Fever](./Cabin%20Fever.md) if it goes above 125 hours out of the past 150 hours.

When suffering from Cabin Fever Risk, working on an item at a [Work Bench](./Work%20Bench.md)/[Ammunition Work Bench](./Ammunition%20Work%20Bench.md)/reading a [Research Book](./Research%20Book.md)/[Cooking](./Cooking.md)/Repairing WILL increase the Affliction Risk meter, despite the fact that the survivor is keeping their mind occupied at the time.

### Special Locations

There are also a few exterior buildings that give a warmth bonus and do not progress Cabin Fever despite appearing to be an indoor space:

- The outdoor room with the bed at the [Maintenance Yard](./Maintenance%20Yard.md)
- Break room at [Last Resort Cannery](./Last%20Resort%20Cannery.md)
- Room outside the [Workshop](./Workshop.md)
- [Stone Church](./Stone%20Church.md)

The following areas do progress Cabin Fever.

- [Mountaineer's Hut](./Mountaineer%27s%20Hut.md)
- [Forgotten Exploration](./Forgotten%20Exploration.md)
- The front porch of the [Pleasant Valley Farmstead](./Pleasant%20Valley%20Farmstead.md)
- The maintenance hallway in the south tunnel in [Far Range Branch Line](./Far%20Range%20Branch%20Line.md)

## Treatment

Spending more time outdoors will gradually cause the Risk to subside. Sleeping in [Warm Caves](./Warm%20Cave.md) or [Snow Shelters](./Snow%20Shelter.md) is generally the safest way to remove Cabin Fever Risk. Great care should be taken to not return to the indoors too quickly once the Risk has subsided, or the Risk will return. Failure to manage Cabin Fever Risk will result in the player contracting [Cabin Fever](./Cabin%20Fever.md).

Some [Cooking](./Cooking.md) Recipes increases the amount of time considered as spent outdoors when consumed, based on the amount eaten. However, they require many ingredients to make and give only a few hours of additional time at most.

| Item | Name |
| --- | --- |
| [Breyerhouse Pie icon](../assets/Breyerhouse_Pie_icon.png) | [Breyerhouse Pie](./Breyerhouse%20Pie.md) |
| [Coastal Fishcakes icon](../assets/Coastal_Fishcakes_icon.png) | [Coastal Fishcakes](./Coastal%20Fishcakes.md) |
| [Dockworker's Pie icon](../assets/Dockworker%27s_Pie_icon.png) | [Dockworker's Pie](./Dockworker%27s%20Pie.md) |
| [Prepper's Pie icon](../assets/Prepper%27s_Pie_icon.png) | [Prepper's Pie](./Prepper%27s%20Pie.md) |
| [Stalker's Pie icon](../assets/Stalker%27s_Pie_icon.png) | [Stalker's Pie](./Stalker%27s%20Pie.md) |

## Update history

- UPDATE **21 April 2016 ([Update](./The%20Long%20Dark%20%E2%80%94%20Early%20Access%20Changelog.md#0.321_%28Tireless_Menace_Update%29)):**
  - Added to game.
- HOTFIX **23 April 2016 ([Update](./The%20Long%20Dark%20%E2%80%94%20Early%20Access%20Changelog.md#0.326)):**
  - Added a grace period for Voyageur and Stalker and removed the affliction entirely on Pilgrim.
- UPDATE **24 June 2024 ([Update](./The%20Long%20Dark%20%E2%80%94%20Changelog.md#2.28_%28Steam,_Epic_Games,_Xbox,_PlayStation%29_%28TFTFT_PART_FIVE:_Last_Horizon_Update%29)):**
  - Added the Settled Mind [Feat](./Feats.md) to the game, which eliminates Cabin Fever Risk.
