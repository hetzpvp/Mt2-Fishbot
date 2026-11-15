/*
 * d3dprobe - a stand-in for the Metin2 client when testing the Linux port.
 *
 * A windowed Direct3D 9 app (the API the game uses), so under Wine it goes
 * through the same rendering and presentation path as the real client.
 * It draws a fixed test picture into an 800x600 client area:
 *
 *   magenta 10x10 square  at client (0,0)        -> capture origin check
 *   yellow  10x10 square  at client (790,590)    -> capture size check
 *   minigame panel        at client (100,100)-(400,300), RGB (37,147,220)
 *   fish 20x12 block      moving inside the panel, RGB (55,92,120)
 *
 * The panel and fish use the stock minigame colors (fish_detector.py
 * DetectionProfile), so the real detector must find them in a capture: that
 * proves the colors survive Wine + the desktop unchanged.
 *
 * and prints every input event it receives on stdout, in client coordinates:
 *
 *   READY <hwnd>
 *   ACTIVE <0|1>
 *   LDOWN x y / LUP x y / RDOWN x y / RUP x y
 *   KEYDOWN vk / KEYUP vk
 *
 * Build (Fedora: mingw64-gcc):
 *   x86_64-w64-mingw32-gcc -O2 -o d3dprobe.exe d3dprobe.c -ld3d9 -lgdi32 -luser32
 * Run:
 *   wine d3dprobe.exe ["window title"] [max seconds, default 120]
 *
 * It exits when its stdin reaches end-of-file (the test closes the pipe) or
 * after the time limit, so it never has to be killed with `wineserver -k`,
 * which would also end every other program in the Wine prefix (the game).
 */
#include <windows.h>
#include <windowsx.h>
#include <d3d9.h>
#include <stdio.h>
#include <stdlib.h>

#define CW 800
#define CH 600

static IDirect3DDevice9 *dev;

static void out(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    printf("\n");
    fflush(stdout);
}

static DWORD WINAPI stdin_watch(LPVOID unused)
{
    char buf[256];
    (void)unused;
    while (fgets(buf, sizeof(buf), stdin))
        ;
    ExitProcess(0);
    return 0;
}

static LRESULT CALLBACK proc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    switch (m) {
    case WM_LBUTTONDOWN: out("LDOWN %d %d", GET_X_LPARAM(l), GET_Y_LPARAM(l)); return 0;
    case WM_LBUTTONUP:   out("LUP %d %d", GET_X_LPARAM(l), GET_Y_LPARAM(l)); return 0;
    case WM_RBUTTONDOWN: out("RDOWN %d %d", GET_X_LPARAM(l), GET_Y_LPARAM(l)); return 0;
    case WM_RBUTTONUP:   out("RUP %d %d", GET_X_LPARAM(l), GET_Y_LPARAM(l)); return 0;
    case WM_KEYDOWN:     out("KEYDOWN %u", (unsigned)w); return 0;
    case WM_KEYUP:       out("KEYUP %u", (unsigned)w); return 0;
    case WM_ACTIVATE:    out("ACTIVE %d", LOWORD(w) != WA_INACTIVE); break;
    case WM_DESTROY:     PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}

static void fill(int x1, int y1, int x2, int y2, D3DCOLOR c)
{
    D3DRECT r = { x1, y1, x2, y2 };
    IDirect3DDevice9_Clear(dev, 1, &r, D3DCLEAR_TARGET, c, 1.0f, 0);
}

int main(int argc, char **argv)
{
    wchar_t title[256] = L"Metin2 Probe";
    DWORD max_ms = 120000;
    if (argc > 1)
        MultiByteToWideChar(CP_UTF8, 0, argv[1], -1, title, 256);
    if (argc > 2)
        max_ms = (DWORD)atoi(argv[2]) * 1000;
    CreateThread(NULL, 0, stdin_watch, NULL, 0, NULL);

    HINSTANCE inst = GetModuleHandleW(NULL);
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = proc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.lpszClassName = L"d3dprobe";
    RegisterClassW(&wc);

    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    RECT wr = { 0, 0, CW, CH };
    AdjustWindowRect(&wr, style, FALSE);
    HWND hwnd = CreateWindowW(L"d3dprobe", title, style, 200, 150,
                              wr.right - wr.left, wr.bottom - wr.top, NULL, NULL, inst, NULL);
    ShowWindow(hwnd, SW_SHOW);

    IDirect3D9 *d3d = Direct3DCreate9(D3D_SDK_VERSION);
    D3DPRESENT_PARAMETERS pp = {0};
    pp.Windowed = TRUE;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.BackBufferFormat = D3DFMT_UNKNOWN;
    pp.BackBufferWidth = CW;
    pp.BackBufferHeight = CH;
    pp.hDeviceWindow = hwnd;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_ONE;
    if (FAILED(IDirect3D9_CreateDevice(d3d, D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, hwnd,
                                       D3DCREATE_SOFTWARE_VERTEXPROCESSING, &pp, &dev))) {
        out("ERROR CreateDevice");
        return 1;
    }
    out("READY %p", (void *)hwnd);

    MSG msg;
    DWORD t0 = GetTickCount();
    for (;;) {
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT)
                return 0;
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        DWORD t = GetTickCount() - t0;
        if (t > max_ms)
            return 0;
        int sx = 110 + (int)((t / 4) % 260);

        IDirect3DDevice9_Clear(dev, 0, NULL, D3DCLEAR_TARGET, D3DCOLOR_XRGB(40, 40, 40), 1.0f, 0);
        fill(0, 0, 10, 10, D3DCOLOR_XRGB(255, 0, 255));
        fill(CW - 10, CH - 10, CW, CH, D3DCOLOR_XRGB(255, 255, 0));
        fill(100, 100, 400, 300, D3DCOLOR_XRGB(37, 147, 220));
        fill(sx, 194, sx + 20, 206, D3DCOLOR_XRGB(55, 92, 120));
        IDirect3DDevice9_Present(dev, NULL, NULL, NULL, NULL);
    }
}
