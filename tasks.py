import random
import subprocess


def get_task_for_job(job, strength, handy, stamina, name, other_names):
    celebration_messages = [
        "The whole farm celebrated with a hamburger bubble-up meal!",
        "Everyone cheered and started to square-dance!",
        "The cows mooed in approval!",
        "A feast of fresh cornbread and raccoon BBQ was served!",
        "The chickens laid eggs in celebration!",
        "Fireworks lit up the night sky in the shape of a sexy chicken!",
        "The local farmer's market gave them a medal!",
        "The farmers celebrated with some of Aunt Myrna's Party cheese salad!",
        "The town declared it a holiday!",
        "The scarecrow even did a happy dance!"
    ]

    failure_messages = [
        "letting down the farm. Everyone shook their heads in disappointment.",
        "causing chaos and making a huge mess! Darn...",
        "so now the cows are laughing at them.",
        "so Farmer Al had to step in to fix the mess.",
        "even Ben the Cow had to back down after seeing that.",
        "so a chicken stole their hat in protest.",
        "so now the whole barn smells like failure.",
        "and they even tripped over a rake while trying again.",
        "so now a goat is causing chaos in protest.",
        "so the mayor has banned them from barn duties for a week.",
        "so the farm dog buried their tools in shame.",
        "clearly they weren't trying their best..."
    ]
    
    if job not in ["Fix Meiser", "Lift Tender", "Speed Runner"]:
        return 0, [f"{name} is on the bench and did not perform any tasks."]

    results = []
    points = 0  # Initialize points

    if job == "Lift Tender":
        taskstr = random.randint(1, 3)
        if taskstr == 1:
            task_name = "Lift Tha Hay"
            rollstr = random.randint(4, 15)
            failmsg = [
                "but the haybales got the best of them.",
                "but they got squashed like a bug.",
                "but the haybales said 'nar nar'."
            ]
            winmsg = [
                "and he lifted the fuck outta those bales.",
                "and he actually did it...I didn't think he had it in him.",
                "and they succeeded! Bro thinks he's hercules or something."
            ]
            if rollstr < strength:
                result = "yippee"
                points = 1 + (strength - rollstr)
            else:
                result = "fail"
        elif taskstr == 2:
            task_name = "Push Tha Car"
            rollstr = random.randint(2, 13)
            failmsg = [
                "but the car didn't budge.",
                "but it did not move an inch.",
                "but the car stayed put. Sounds like someone forgot to put it in neutral."
                ]
            winmsg = [
                "and defied physics, expectations, and his chiropractor's advice!",
                "and succeeded! Now, who's pushing him?",
                "and now it calls him 'daddy'."
                ]
            if rollstr < strength:
                result = "yippee"
                points = 1 + (strength - rollstr)
            else:
                result = "fail"
        else:
            task_name = "Shovel Tha Manure"
            rollstr = random.randint(1, 16)
            rollsta = random.randint(2, 10)
            failmsg = [
                "but now it's a 'scent-sational' disaster!",
                "but his efforts belong in the same pile.",
                "but the whole pile exploded in his face like a dirty prank from the universe.",
                "but he failed. That's wasn't cowshit... that was BULLSHIT!"
                ]
            winmsg = [
                "and successfully cleaned it all up. We could call him the scatman today thats for sure.",
                "and it turned out alright after all. No shit stains on the new overalls.",
                "and showed that manure who's boss.",
                "and even though he succeeded, nothing will mask that smell..."
                ]
            if rollstr < strength and rollsta < stamina:
                result = "yippee"
                points = 1 + (strength - rollstr) + (stamina - rollsta)
            else:
                result = "fail"

    elif job == "Fix Meiser":
        taskhan = random.randint(1, 3)
        if taskhan == 1:
            task_name = "Fix Tha Tractor"
            rollhan = random.randint(3, 15)
            failmsg = [
                "but somehow made it worse, now it's a lawn ornament.",
                "but accidentally transformed it into an expensive paperweight.",
                "but now it's smoking like Josh with a bong."
                ]
            winmsg = [
                "and it worked, off to the Tractor Grand Prix (hearts in a cornfield reference).",
                "and now it's purring like a kitten... maybe he'll make me his little kitten next XD.",
                "and the tractor started playing country music on its own.",
                "and its running like new. All he needs now is some new boots to run right past her. Yup, on his tractor."
                ]
            if rollhan < handy:
                result = "yippee"
                points = 1 + (handy - rollhan)
            else:
                result = "fail"
        elif taskhan == 2:
            task_name = "Repair Tha Barn"
            rollhan = random.randint(3, 12)
            failmsg = [
                "but accidentally created a whole new opening for the cows to fucking escape.",
                "but instead built a new home for a family of squirrels who are now demanding rent.",
                "but turned it into a 'do-it-yourself' disaster that'll be on HGTV's blooper reel.",
                "but failed. Someone call in the Amish like that one episode of Family Guy."
                ]
            winmsg = [
                "and accidentally created the eighth wonder of the world.",
                "and somehow made it look like a five-star resort for farm animals.",
                "and made the place look like a palace. Now the Amish are sending him contracts."
                ]
            if rollhan < handy:
                result = "yippee"
                points = 1 + (handy - rollhan)
            else:
                result = "fail"
        else:
            task_name = "Build Tha Fence"
            rollhan = random.randint(3, 15)
            failmsg = [
                "but the only thing he nailed was his own finger. Now a sheep is nailing his wife.",
                "but instead created a barricade of disappointment and splinters.",
                "but ended up creating a luxury dog door for every farm animal...mission failed!",
                "but it turned into a farm-wide invitation for every animal to go on an adventure.",
                "but forgot to put up the posts first, so it's basically just a bunch of random boards lying on the ground. Fucking retard."
                ]
            winmsg = [
                "and nailed it, literally. Now he's going to nail his fat wife and maybe a chicken.",
                "and it worked. The lad sure knows his way around the wood.",
                "and now its so perfect, Pinocchio calls him everynight to treat his wood the same way.",
                "and succeeded. That required sobriety, lets keep it this way."
                ]
            if rollhan < handy:
                result = "yippee"
                points = 1 + (handy - rollhan)
            else:
                result = "fail"

    elif job == "Speed Runner":
        tasksta = random.randint(1, 4)  # Changed to 4 to fix duplicate case
        if tasksta == 1:
            task_name = "Milk Tha Cow"
            rollsta = random.randint(2, 14)
            failmsg = [
                "but the cow looked at him and said, 'Nice try, fuckface'.",
                "but the cow looked at him and said, 'Do i looked like the Dairy Queen to you'?",
                "but the cow said 'You better take me out for dinner first'.",
                "but failed. If he can't milk the cow, I guess he has to leave the farm.",
                "but it's tits had nothing left to give...Fresh burgers tomorrow?"
                ]
            winmsg = [
                "and received gallons of milk. No more self-sucking!",
                "and became the dairy queen.",
                "and succeeded. Probably due to saying 'Got Milk?' before going in.",
                "and it worked! The cow even sent him a titty pic later."
                ]
            if rollsta < stamina:
                result = "yippee"
                points = 1 + (stamina - rollsta)
            else:
                result = "fail"
        elif tasksta == 2:
            task_name = "Mow Tha Lawn"
            rollsta = random.randint(1, 12)
            failmsg = [
                "but somehow turned the lawnmower into a runaway go-kart.",
                "but the lawnmower refused to start.",
                "but it got caught on some stones."
                ]
            winmsg = [
                "and made it look so good that even the weeds started lining up to apologize.",
                "and finished so fast, even the grass didn't realize it had been cut.",
                "and succeeded. Everyone starting clapping, but they weren't moving their hands."
                ]
            if rollsta < stamina:
                result = "yippee"
                points = 1 + (stamina - rollsta)
            else:
                result = "fail"
        elif tasksta == 3:
            task_name = "Harvest tha Crops"
            rollsta = random.randint(1, 12)
            tasktype = 'crops'
            failmsg = [
                "but ended up in a brawling with a angry scarecrow.",
                "but accidentally harvested his own damn boot. Fucking dumbass.",
                "but couldn't find the damn tractor.",
                "but out came the children of the corn and prevented him from doing so."
                ]
            winmsg = [
                "and he succeeded. I guess country girls DO make do.",
                "and now the soil is writing him thank-you notes.",
                "and now the field looks like a diggity-darn masterpiece."
                ]
            if rollsta < stamina:
                result = "yippee"
                points = 1 + (stamina - rollsta)
            else:
                result = "fail"
        else:
            task_name = "Chase Tha Coyote"
            rollsta = random.randint(6, 14)
            failmsg = [
                "but the coyote hit him with a crate of dynamite. Classic Wile E. move.",
                "but his sneakers were no match for the ACME jetpack that just left him in the dust.",
                "but ran headfirst into a wall that was painted to look like a tunnel."
                ]
            winmsg = [
                "and succeeded! He didn't even have to call in Ben the Cow this time.",
                "and chased him all the way home where the coyote invited him inside, but he missed the signals.",
                "and caught it! Coyote sliders for dinner anyone? Hey, we don't waste any food in these parts."
                ]
            if rollsta < stamina:
                result = "yippee"
                points = 1 + (stamina - rollsta)
            else:
                result = "fail"
    
    results = []

    if result == "yippee":
        results.append(f"{name} tried their best to {task_name} {random.choice(winmsg)} {random.choice(celebration_messages)}")
    else:
        fail_message = random.choice(failmsg) if 'failmsg' in locals() else "but they failed miserably."
        results.append(f"{name} tried their best to {task_name} {fail_message} {' and '.join(other_names)} were both very disappointed in {name}.")

    return points, results
