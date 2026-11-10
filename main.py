import asyncio

async def main():
    global running
    while running:
        for event in pygame.event.get():
            handle_event(event)      # твой обработчик событий
        update()                     # твой апдейт мира
        draw()                       # твоя отрисовка
        pygame.display.flip()
        await asyncio.sleep(0)       # отдаёт управление браузеру

if __name__ == "__main__":
    asyncio.run(main())
