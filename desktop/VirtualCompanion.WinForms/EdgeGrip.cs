namespace VirtualCompanion.Desktop;

/// <summary>WM_NCHITTEST 命中码（仅窗口边缘需要的部分）。</summary>
internal enum HitTest
{
    Left = 10,
    Right = 11,
    Top = 12,
    TopLeft = 13,
    TopRight = 14,
    Bottom = 15,
    BottomLeft = 16,
    BottomRight = 17,
}

/// <summary>
/// 无边框窗口的边缘缩放把手：不绘制内容，背景使用窗体的 Light/Dark 纯色。
/// 按下后自行捕获鼠标并按方向调整窗体边界 —— 不依赖系统 WS_THICKFRAME
/// （FormBorderStyle.None 的窗口没有该样式，系统 resize 循环不会启动）。
/// </summary>
internal sealed class EdgeGrip : Control
{
    private readonly HitTest _hitTest;
    private bool _resizing;
    private Point _startScreen;
    private Rectangle _startBounds;

    public event EventHandler? ResizeStarted;
    public event EventHandler? ResizeCompleted;

    public EdgeGrip(HitTest hitTest, Cursor cursor)
    {
        _hitTest = hitTest;
        Cursor = cursor;
        SetStyle(ControlStyles.Selectable, false);
    }

    protected override void OnPaintBackground(PaintEventArgs e)
    {
        e.Graphics.Clear(BackColor);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        // 不绘制内容。
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        if (e.Button != MouseButtons.Left || FindForm() is not { } form)
        {
            return;
        }

        // 最大化/最小化时不做缩放。
        if (form.WindowState != FormWindowState.Normal)
        {
            return;
        }

        _resizing = true;
        _startScreen = PointToScreen(e.Location);
        _startBounds = form.Bounds;
        Capture = true;
        ResizeStarted?.Invoke(this, EventArgs.Empty);
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        if (!_resizing || FindForm() is not { } form)
        {
            return;
        }

        var current = PointToScreen(e.Location);
        var dx = current.X - _startScreen.X;
        var dy = current.Y - _startScreen.Y;
        form.Bounds = ComputeBounds(_startBounds, dx, dy, form.MinimumSize);
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        EndResize();
    }

    protected override void OnMouseCaptureChanged(EventArgs e)
    {
        base.OnMouseCaptureChanged(e);
        if (!Capture)
        {
            EndResize();
        }
    }

    private void EndResize()
    {
        if (!_resizing)
        {
            return;
        }
        _resizing = false;
        if (Capture)
        {
            Capture = false;
        }
        ResizeCompleted?.Invoke(this, EventArgs.Empty);
    }

    private bool LeftEdge => _hitTest is HitTest.Left or HitTest.TopLeft or HitTest.BottomLeft;
    private bool RightEdge => _hitTest is HitTest.Right or HitTest.TopRight or HitTest.BottomRight;
    private bool TopEdge => _hitTest is HitTest.Top or HitTest.TopLeft or HitTest.TopRight;
    private bool BottomEdge => _hitTest is HitTest.Bottom or HitTest.BottomLeft or HitTest.BottomRight;

    private Rectangle ComputeBounds(Rectangle start, int dx, int dy, Size minimumSize)
    {
        var bounds = start;
        var minWidth = minimumSize.Width > 0 ? minimumSize.Width : 0;
        var minHeight = minimumSize.Height > 0 ? minimumSize.Height : 0;

        if (LeftEdge)
        {
            bounds.X = Math.Min(start.X + dx, start.Right - minWidth);
            bounds.Width = start.Right - bounds.X;
        }
        else if (RightEdge)
        {
            bounds.Width = Math.Max(start.Width + dx, minWidth);
        }

        if (TopEdge)
        {
            bounds.Y = Math.Min(start.Y + dy, start.Bottom - minHeight);
            bounds.Height = start.Bottom - bounds.Y;
        }
        else if (BottomEdge)
        {
            bounds.Height = Math.Max(start.Height + dy, minHeight);
        }

        return bounds;
    }
}
