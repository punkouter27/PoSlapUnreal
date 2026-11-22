// The URLab / MuJoCo headers this module uses, in one place. Two of them trip MSVC warnings in every file that includes
// them: MuJoCo's mjsan.h redefines the ASAN_(UN)POISON_MEMORY_REGION macros MSVC's sanitizer header already defines
// (C4005), and URLab's generated option defaults initialise floats from doubles (C4305). Both are third-party headers
// (Plugins/UnrealRoboticsLab, recreated by scripts/setup_urlab.ps1), so exactly those two warnings are silenced, only
// around these includes, and this module builds with zero warnings of its own.
#pragma once

#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable : 4005 4305)
#endif

#include "MuJoCo/Components/Controllers/MjArticulationController.h"
#include "MuJoCo/Components/Geometry/MjGeom.h"
#include "MuJoCo/Core/AMjManager.h"
#include "MuJoCo/Core/MjPhysicsEngine.h"
#include "MuJoCo/Core/MjRenderSnapshot.h"
#include "MuJoCo/Utils/MjUtils.h"

#ifdef _MSC_VER
#pragma warning(pop)
#endif
