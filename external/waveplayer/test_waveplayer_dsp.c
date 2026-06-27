#include <assert.h>
#include <math.h>
#include <stdio.h>

typedef float t_float;   /* stand in for m_pd.h's t_float in the test */
#include "waveplayer_dsp.h"

static int close_enough(float x, float y) { return fabsf(x - y) < 1e-5f; }

int main(void) {
    /* frac=0 returns b exactly */
    assert(close_enough(wp_interp4(0.f, 1.f, 2.f, 3.f, 0.0f), 1.0f));
    /* frac=1 returns c exactly */
    assert(close_enough(wp_interp4(0.f, 1.f, 2.f, 3.f, 1.0f), 2.0f));
    /* linear ramp: midpoint is the linear value 1.5 */
    assert(close_enough(wp_interp4(0.f, 1.f, 2.f, 3.f, 0.5f), 1.5f));
    /* non-linear data: hand-computed expected value */
    assert(close_enough(wp_interp4(0.f, 0.f, 1.f, 0.f, 0.5f), 0.5625f));
    printf("wp_interp4 tests passed\n");
    return 0;
}
