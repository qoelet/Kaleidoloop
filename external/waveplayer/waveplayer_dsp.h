#ifndef WAVEPLAYER_DSP_H
#define WAVEPLAYER_DSP_H

/* 4-point polynomial interpolation, verbatim from Pd's tabread4~
 * (as used in the original C&G waveplayer~). a,b,c,d are consecutive
 * samples; frac is the fractional position in [0,1) between b and c. */
static inline t_float wp_interp4(t_float a, t_float b, t_float c,
                                 t_float d, t_float frac) {
    t_float cminusb = c - b;
    return b + frac * (
        cminusb - 0.1666667f * (1.f - frac) * (
            (d - a - 3.0f * cminusb) * frac + (d + 2.0f * a - 3.0f * b)
        )
    );
}

#endif
