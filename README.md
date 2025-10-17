## Getting Started

These instructions will help you set up and run the project locally.

### Prerequisites

- Python 3.x installed on your machine.
- Git (optional, for cloning the repository).

### Installation

1. Clone the repository (if not already cloned):

```bash
git clone <your-repository-url>
cd <repository-folder>

## Init Project
Download the ADB keyboard.
```bash
adb install adb_keyboard.apk
adb -s <device_id> install app.apk
adb -s emulator-5554 shell am broadcast -a ADB_INPUT_TEXT --es text '测试一下'

Set it as the default settings.