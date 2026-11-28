import 'package:flutter_test/flutter_test.dart';
import 'package:lain/equalizer.dart';

void main(){
  test('presets only apply conservative gain to actual frequency bands',(){
    expect(presetGain('flat',80),0);
    expect(presetGain('bass',80),3);
    expect(presetGain('bass',6000),-1);
    expect(presetGain('voice',1000),2);
    expect(presetGain('treble',8000),3);
    for(final preset in ['flat','bass','voice','treble']){
      for(final hz in [60.0,230.0,910.0,3600.0,14000.0]){
        expect(presetGain(preset,hz).abs(),lessThanOrEqualTo(6));
      }
    }
  });
}
